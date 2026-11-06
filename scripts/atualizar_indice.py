#!/usr/bin/env python3
"""
scripts/atualizar_indice.py

Atualiza o indice.json, o "sumário" do repositório que o app lê no máximo
uma vez por dia para saber o que mudou, sem baixar tudo (contrato no doc
"RPM — Mapa de dados"):

    {
      "formato": 1,
      "gerado_em": "2026-11-06T20:00:00-03:00",
      "arquivos": {
        "camadas/PE.json": {"versao": 202611062000, "bytes": 812345,
                            "sha256": "…", "bbox": [-9.48, -41.36, -3.83, -32.39]},
        "eventos/agenda.json": {"versao": …, "bytes": …, "sha256": "…"}
      }
    }

Regras:
  - olha TODOS os arquivos .json de camadas/ e de eventos/ (os dois
    geradores chamam este mesmo script, e um nunca apaga a linha do outro);
  - a versão (AAAAMMDDHHMM, horário de Brasília) só sobe para o arquivo
    cujo conteúdo mudou (sha256 diferente); os outros mantêm a versão, e o
    app não baixa de novo;
  - bytes e sha256 são do arquivo como está no repositório. O GitHub Pages
    entrega comprimido (gzip), mas o app descomprime antes de conferir;
  - bbox (só nas camadas): o retângulo [sul, oeste, norte, leste] que cobre
    todos os lugares do arquivo, para o app saber quais estados baixar sem
    guardar o contorno deles;
  - se nada mudou, o indice.json nem é regravado (nada para commitar).

Uso (a partir da raiz do repositório):

    python3 scripts/atualizar_indice.py
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# A versão do formato do índice (a mesma regra do formato das camadas).
FORMATO = 1

# As pastas que entram no índice.
PASTAS = ["camadas", "eventos"]

# Horário de Brasília (o Brasil não tem mais horário de verão). Fuso fixo:
# não depende da tabela de fusos do sistema.
BRASILIA = timezone(timedelta(hours=-3))

RAIZ = Path(__file__).resolve().parent.parent


def sha256(conteudo: bytes) -> str:
    """A "impressão digital" do arquivo: qualquer byte diferente muda tudo.
    O app confere esse valor depois de baixar, antes de trocar o que tem."""
    return hashlib.sha256(conteudo).hexdigest()


def bbox_das_camadas(conteudo: bytes) -> list[float] | None:
    """O retângulo [sul, oeste, norte, leste] que cobre todos os lugares de
    um arquivo de camadas (None se não houver nenhum).

    Arredonda PARA FORA, com 3 casas (cerca de 100 m): o sul e o oeste
    para baixo (floor) e o norte e o leste para cima (ceil). Assim nenhum
    lugar fica fora do retângulo por causa do arredondamento."""
    dados = json.loads(conteudo.decode("utf-8"))
    lats, lons = [], []
    for itens in dados.get("camadas", {}).values():
        for lugar in itens:
            lats.append(lugar["lat"])
            lons.append(lugar["lon"])
    if not lats:
        return None

    # O round(…, 6) antes do floor/ceil evita a "sujeira" da vírgula
    # flutuante: -34.9 * 1000 pode dar -34900.000000000004, e o floor
    # jogaria para -34.901 sem motivo.
    def baixo(v: float) -> float:
        return math.floor(round(v * 1000, 6)) / 1000

    def cima(v: float) -> float:
        return math.ceil(round(v * 1000, 6)) / 1000

    return [baixo(min(lats)), baixo(min(lons)), cima(max(lats)), cima(max(lons))]


def versao_agora(agora: datetime) -> int:
    """A versão no padrão AAAAMMDDHHMM, como número (ex.: 202611062000).
    Número para o app comparar com "maior que"."""
    return int(agora.astimezone(BRASILIA).strftime("%Y%m%d%H%M"))


def montar_arquivos(raiz: Path, anterior: dict, agora: datetime) -> dict:
    """Monta o bloco "arquivos" do índice, olhando o disco.

    Para cada .json das [PASTAS]: calcula bytes e sha256; se o sha256 for o
    mesmo do índice [anterior], mantém a versão antiga; se mudou (ou é
    arquivo novo), usa a versão de [agora]. Arquivo que sumiu do disco sai
    do índice. As chaves saem em ordem alfabética (índice estável)."""
    versao_nova = versao_agora(agora)
    arquivos = {}
    for pasta in PASTAS:
        for caminho in sorted((raiz / pasta).glob("*.json")):
            nome = caminho.relative_to(raiz).as_posix()  # "camadas/PE.json"
            conteudo = caminho.read_bytes()
            impressao = sha256(conteudo)
            antes = anterior.get(nome, {})
            entrada = {
                "versao": antes["versao"]
                if antes.get("sha256") == impressao and "versao" in antes
                else versao_nova,
                "bytes": len(conteudo),
                "sha256": impressao,
            }
            if pasta == "camadas":
                if (bbox := bbox_das_camadas(conteudo)) is not None:
                    entrada["bbox"] = bbox
            arquivos[nome] = entrada
    return arquivos


def atualizar(raiz: Path = RAIZ, agora: datetime | None = None) -> bool:
    """Lê o indice.json atual (se houver), monta o novo e grava só se o
    bloco "arquivos" mudou. Devolve True se gravou, False se nada mudou.
    [agora] pode ser fixado nos testes."""
    agora = agora or datetime.now(BRASILIA)
    caminho = raiz / "indice.json"
    anterior = {}
    if caminho.exists():
        anterior = json.loads(caminho.read_text(encoding="utf-8")).get("arquivos", {})

    arquivos = montar_arquivos(raiz, anterior, agora)
    if arquivos == anterior:
        print("indice.json: nada mudou.")
        return False

    indice = {
        "formato": FORMATO,
        "gerado_em": agora.astimezone(BRASILIA).isoformat(timespec="seconds"),
        "arquivos": arquivos,
    }
    temporario = caminho.with_suffix(".json.tmp")
    temporario.write_text(
        json.dumps(indice, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporario.replace(caminho)

    # O resumo: o que mudou desde o índice anterior.
    for nome, entrada in arquivos.items():
        if anterior.get(nome, {}).get("sha256") != entrada["sha256"]:
            kb = f"{entrada['bytes'] / 1024:,.0f}".replace(",", ".")
            print(f"  atualizado: {nome} (versão {entrada['versao']}, {kb} KB)")
    for nome in anterior.keys() - arquivos.keys():
        print(f"  removido:   {nome}")
    print("indice.json gravado.")
    return True


if __name__ == "__main__":
    atualizar()
    sys.exit(0)
