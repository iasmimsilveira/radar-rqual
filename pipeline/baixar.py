"""Baixa todas as bases públicas usadas pelo Radar RQUAL para a pasta raw/.

Todas as fontes são oficiais e abertas:
- Anatel: indicadores RQUAL, acessos da telefonia móvel, reclamações e Meu Município (cobertura)
- ANEEL: DEC/FEC mensal por conjunto elétrico e relação conjunto-município
- INPE: focos de queimada mensais
- IBGE: malhas municipal e estadual
"""
import datetime
import os
import subprocess

UA = "Mozilla/5.0 (RadarRQUAL; dados abertos)"
ANATEL = "https://www.anatel.gov.br/dadosabertos/paineis_de_dados"
ANEEL = "https://dadosabertos.aneel.gov.br/dataset"

ARQUIVOS = {
    "raw/anatel/indicadores_rqual.zip": f"{ANATEL}/qualidade/indicadores_rqual.zip",
    "raw/anatel/acessos_telefonia_movel.zip": f"{ANATEL}/acessos/acessos_telefonia_movel.zip",
    "raw/anatel/consumidor_reclamacoes.zip": f"{ANATEL}/consumidor/consumidor_reclamacoes.zip",
    "raw/anatel/meu_municipio.zip": f"{ANATEL}/meu_municipio/meu_municipio.zip",
    "raw/aneel/continuidade_2020_2029.parquet": f"{ANEEL}/d5f0712e-62f6-4736-8dff-9991f10758a7/resource/d7f70fb1-725c-4748-afeb-65c6a78df550/download/indicadores-continuidade-coletivos-2020-2029.parquet",
    "raw/aneel/indqual_municipio.csv": f"{ANEEL}/db9c9f60-b3b5-4504-9dfe-2637922d53ce/resource/3f841488-80a8-42f2-a6ca-e0c593b228de/download/indqual-municipio.csv",
    "raw/ibge/br_mun.topojson": "https://servicodados.ibge.gov.br/api/v3/malhas/paises/BR?formato=application/json&qualidade=minima&intrarregiao=municipio",
    "raw/ibge/br_uf.topojson": "https://servicodados.ibge.gov.br/api/v3/malhas/paises/BR?formato=application/json&qualidade=minima&intrarregiao=UF",
    "raw/geo/countries-50m.json": "https://cdn.jsdelivr.net/npm/world-atlas@2.0.2/countries-50m.json",
}


def baixar(destino: str, url: str, obrigatorio: bool = True) -> None:
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    print("baixando", destino)
    r = subprocess.run(["curl", "-sS", "-f", "-L", "-A", UA, "-o", destino, url])
    if r.returncode != 0:
        if os.path.exists(destino):
            os.remove(destino)
        if obrigatorio:
            raise SystemExit(f"falha ao baixar {url}")


def main():
    for destino, url in ARQUIVOS.items():
        baixar(destino, url)
    hoje = datetime.date.today()
    for ano in range(2024, hoje.year + 1):
        for mes in range(1, 13):
            if (ano, mes) > (hoje.year, hoje.month):
                break
            nome = f"focos_mensal_br_{ano}{mes:02d}.csv"
            baixar(f"raw/inpe/{nome}",
                   f"https://dataserver-coids.inpe.br/queimadas/queimadas/focos/csv/mensal/Brasil/{nome}",
                   obrigatorio=False)


if __name__ == "__main__":
    main()
