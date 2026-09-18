"""Estima a taxa de substituicao na ruptura, por canal, a partir do painel diario.

Uso: python scripts/estimar_substituicao.py [saida.csv]

E a base empirica do `fator_perda_ruptura_*` (backend/config.py). Resultado
de 2026-09: e-commerce substituicao ~2-6% (fator ~0,95); lojas nao
identificavel (IC90 -49%..+60% no recorte limpo). Duas armadilhas que este
desenho corrige e que uma comparacao ingenua (dias com x sem estoque na media
de dois anos) nao corrige: tendencia (rupturas concentradas em fim de linha) e
composicao (irmaos da mesma marca faltam juntos; o que sobra disponivel e o de
giro baixo). Por isso cada irmao e comparado com a sua propria taxa nas
janelas de +-W dias em torno do episodio.

Substituicao por episodio, irmao a irmao (controla composicao do grupo).
Para cada episodio de i e cada irmao j: excesso_j = venda_j nos dias do
episodio em que j estava disponivel - taxa_j(controle) x esses dias, com
taxa_j(controle) medida nos dias das janelas +-W em que i E j estavam
disponiveis. Tudo normalizado pelo catalogo do dia."""
import duckdb, numpy as np, pandas as pd, sys
import os
c = duckdb.connect(os.environ.get("DUCKDB_PATH", "data/elevato.duckdb"), read_only=True)
W, DMIN, DMAX, CTRL_MIN = 30, 3, 90, 10
sql = f"""
with prod as (
  select cast(IDSUBPRODUTO as varchar) sku,
         coalesce(DESCRSUBGRUPO,'?')||' | '||coalesce(MARCA,'?')||' | '||
         coalesce(regexp_extract(DESCRCOMPRODUTO, '(\\d+[,.]?\\d*\\s?[Xx]\\s?\\d+[,.]?\\d*)', 1), '') as grupo,
         DESCRSECAO secao from raw_produtos),
uni as (select sku from mart_sku_financeiro where pecas_vendidas > 0),
grp as (select p.sku, p.grupo, p.secao from prod p join uni using (sku)
  where p.grupo in (select grupo from prod join uni using (sku) group by 1 having count(*) >= 2)),
tot as (select data, sum(pecas_ecommerce) te, sum(pecas_lojas) tl from mart_estoque_diario group by 1),
pan as (select e.sku, g.grupo, g.secao, e.data, e.estado_estoque est,
               e.pecas_ecommerce / nullif(t.te,0) pe, e.pecas_lojas / nullif(t.tl,0) pl, t.te, t.tl
        from mart_estoque_diario e join grp g using (sku) join tot t using (data)),
marc as (select sku, data, est, case when est='Sem estoque' and lag(est) over (partition by sku order by data)='Sem estoque' then 0 else 1 end ini from pan),
ilhas as (select sku, data, est, sum(ini) over (partition by sku order by data) ep from marc),
epi as (select sku, ep, min(data) d0, max(data) d1, count(*) dur from ilhas where est='Sem estoque'
        group by 1,2 having count(*) between {DMIN} and {DMAX}),
-- focal nas janelas: demanda esperada de i e n de dias de controle
foc as (
  select e.sku, e.ep, e.d0, e.d1, e.dur, any_value(f.grupo) grupo, any_value(f.secao) secao,
    count(case when f.data < e.d0 and f.est='Disponivel' then 1 end) n_antes,
    count(case when f.data > e.d1 and f.est='Disponivel' then 1 end) n_depois,
    avg(case when f.est='Disponivel' then f.pe end) pe_i, avg(case when f.est='Disponivel' then f.pl end) pl_i,
    avg(case when f.data between e.d0 and e.d1 then f.te end) te_ep, avg(case when f.data between e.d0 and e.d1 then f.tl end) tl_ep
  from epi e join pan f on f.sku=e.sku and f.data between e.d0 - interval {W} day and e.d1 + interval {W} day
  group by 1,2,3,4,5),
foc_ok as (select * from foc where n_antes >= {CTRL_MIN} and n_depois >= {CTRL_MIN}),
-- dias em que o focal estava disponivel (para o controle do irmao)
fdisp as (select sku, data from pan where est='Disponivel'),
-- irmaos j: dentro do episodio e no controle
irm as (
  select fo.sku, fo.ep, j.sku sku_j,
    sum(case when j.data between fo.d0 and fo.d1 and j.est='Disponivel' then 1 else 0 end) dj_ep,
    sum(case when j.data between fo.d0 and fo.d1 and j.est='Disponivel' then j.pe else 0 end) pe_j_ep,
    sum(case when j.data between fo.d0 and fo.d1 and j.est='Disponivel' then j.pl else 0 end) pl_j_ep,
    sum(case when j.data not between fo.d0 and fo.d1 and j.est='Disponivel' and fd.sku is not null then 1 else 0 end) dj_ctl,
    sum(case when j.data not between fo.d0 and fo.d1 and j.est='Disponivel' and fd.sku is not null then j.pe else 0 end) pe_j_ctl,
    sum(case when j.data not between fo.d0 and fo.d1 and j.est='Disponivel' and fd.sku is not null then j.pl else 0 end) pl_j_ctl
  from foc_ok fo join pan j on j.grupo=fo.grupo and j.sku<>fo.sku
       and j.data between fo.d0 - interval {W} day and fo.d1 + interval {W} day
  left join fdisp fd on fd.sku=fo.sku and fd.data=j.data
  group by 1,2,3),
irm_ag as (
  select sku, ep, count(*) n_irm, avg(dj_ep) disp_ep_media, 
    sum(case when dj_ep>0 and dj_ctl>=5 then pe_j_ep - pe_j_ctl/dj_ctl*dj_ep end) exc_e,
    sum(case when dj_ep>0 and dj_ctl>=5 then pl_j_ep - pl_j_ctl/dj_ctl*dj_ep end) exc_l,
    count(case when dj_ep>0 and dj_ctl>=5 then 1 end) n_irm_ok
  from irm group by 1,2)
select fo.*, i.n_irm, i.n_irm_ok, i.disp_ep_media, i.exc_e, i.exc_l
from foc_ok fo join irm_ag i using (sku, ep) where i.n_irm_ok > 0
"""
df = c.execute(sql).df()
print(f"episodios: {len(df)}  skus: {df.sku.nunique()}")
print(f"irmaos por grupo (media): {df.n_irm.mean():.1f}; fracao do episodio com irmao disponivel: {(df.disp_ep_media/df.dur).mean():.2f}")
df["exc_e"] = df.exc_e * df.te_ep; df["exc_l"] = df.exc_l * df.tl_ep
df["esp_e"] = df.pe_i * df.te_ep * df.dur; df["esp_l"] = df.pl_i * df.tl_ep * df.dur

def taxa(s, ex, esp):
    d = s[esp].sum(); return s[ex].sum()/d if d > 0 else np.nan
def boot(s, ex, esp, B=500, seed=1):
    rng = np.random.default_rng(seed); n=len(s)
    return np.nanpercentile([taxa(s.iloc[rng.integers(0,n,n)], ex, esp) for _ in range(B)], [5,95])
def linha(nome, s):
    for canal, ex, esp in (("lojas","exc_l","esp_l"),("ecommerce","exc_e","esp_e")):
        ss = s[s[esp] > 0]
        if len(ss) < 15 or ss[esp].sum() < 30:
            print(f"{nome:40s} {canal:10s} ep={len(ss):5d}  (pouca demanda esperada)"); continue
        t = taxa(ss, ex, esp); lo, hi = boot(ss, ex, esp)
        print(f"{nome:40s} {canal:10s} ep={len(ss):5d} esp={ss[esp].sum():8.0f}pc  subst={t:6.1%} IC90=[{lo:.0%},{hi:.0%}]  fator={1-t:.2f}")
print("\n== TOTAL"); linha("TOTAL", df)
print("\n== por secao")
for sec, s in sorted(df.groupby("secao"), key=lambda x: -x[1].esp_l.sum())[:14]: linha(sec[:40], s)
print("\n== por duracao")
for lab, s in df.groupby(pd.cut(df.dur,[0,7,14,30,90])): linha(str(lab), s)
if len(sys.argv) > 1:
    df.to_csv(sys.argv[1], index=False)
