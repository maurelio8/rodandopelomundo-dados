#!/usr/bin/env python3
"""
scripts/gerar_camadas.py

Gera os arquivos camadas/UF.json (um por estado) com as camadas do mapa do
app Rodando Pelo Mundo (RPM), extraídas do OpenStreetMap pelo servidor
público Overpass:

    pedágios, postos, oficinas de moto, estacionamento de moto, mirantes,
    lombadas e borracharias.

As REGRAS (quais etiquetas contam como posto, lombada etc.) são as mesmas do
app, em lib/calculos/camadas_osm.dart: o mapa não muda de conteúdo quando o
app trocar a busca direta no Overpass por estes arquivos. A única novidade é
a camada de borracharias.

Uso (sempre a partir da raiz do repositório):

    python3 scripts/gerar_camadas.py PE            # só Pernambuco
    python3 scripts/gerar_camadas.py PE PB RN      # alguns estados
    python3 scripts/gerar_camadas.py --todos       # os 27
    python3 scripts/gerar_camadas.py PE --forcar   # grava mesmo com queda grande

Depois de gerar, rode o scripts/atualizar_indice.py, que atualiza o
indice.json (o que o app lê para saber o que mudou).

Só usa a biblioteca padrão do Python (nada de pip install): roda igual no
Fedora e no computador do GitHub (GitHub Actions), quando a tarefa mensal for
ligada.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

# A versão do formato do arquivo. O app ignora arquivo com formato maior do
# que ele entende (regra 4 do contrato no doc "RPM — Mapa de dados"). Só sobe
# quando a estrutura mudar de um jeito que o app antigo não consiga ler.
FORMATO = 1

# Os 27 estados, pela sigla. A área de cada um é achada no OpenStreetMap
# pelo código oficial ISO 3166-2 ("BR-PE"), que fica na fronteira do estado.
UFS = [
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS",
    "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC",
    "SP", "SE", "TO",
]

# Os servidores, na mesma ordem do app: o principal e um espelho.
SERVIDORES = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]

# Como o script se apresenta ao servidor (a regra de uso justo pede um nome
# e um contato).
IDENTIFICACAO = (
    "RodandoPeloMundo-dados/1.0 "
    "(gerador das camadas do app; approdandopelomundo@gmail.com)"
)

# Quanto tempo o SERVIDOR pode gastar numa consulta (segundos). É pedido
# dentro da própria consulta ([timeout:300]).
TEMPO_SERVIDOR = 300
# Quanto tempo o script espera a resposta chegar (um pouco mais que o acima).
TEMPO_HTTP = TEMPO_SERVIDOR + 30
# Pausa entre uma consulta e outra, por educação com o servidor público.
PAUSA_ENTRE_CONSULTAS = 5
# Quantas voltas pelos servidores antes de desistir, e a espera entre elas.
VOLTAS = 3
ESPERA_ENTRE_VOLTAS = 60

# A TRAVA contra resposta pela metade: se uma camada vier com mais de 30% a
# menos do que na versão anterior, o estado não é gravado (a não ser com
# --forcar). Camadas pequenas (menos de 20 lugares) não entram na conta:
# 3 pedágios virarem 2 é normal.
QUEDA_MAXIMA = 0.30
MINIMO_PARA_COMPARAR = 20

# Pastas: o script mora em scripts/, e os arquivos vão para camadas/.
RAIZ = Path(__file__).resolve().parent.parent
PASTA_CAMADAS = RAIZ / "camadas"

# Crédito e licença, gravados dentro de cada arquivo: quem pegar só o
# arquivo já leva junto a origem dos dados (a ODbL pede isso).
FONTE = "© colaboradores do OpenStreetMap"
LICENCA = "ODbL 1.0 — https://opendatacommons.org/licenses/odbl/1-0/"

# Os valores de traffic_calming que contam como LOMBADA (os mesmos do app).
# Ficam de fora o sonorizador (rumble_strip), a chicana e as ilhas.
TIPOS_LOMBADA = {"bump", "hump", "table", "cushion", "yes"}

# As camadas e os "filtros" de cada uma na linguagem do Overpass.
# O nome de cada camada é o MESMO do enum CamadaOsm do app (o .name),
# para o app reconhecer direto. A ordem do dicionário é a ordem no arquivo.
#   - nwr = node, way ou relation (ponto, área ou conjunto). Postos e
#     estacionamentos costumam ser desenhados como área;
#   - lombada é sempre ponto (node);
#   - ,i no fim do filtro de nome = sem diferença de maiúscula/minúscula.
CAMADAS: dict[str, list[str]] = {
    "pedagios": ['nwr["barrier"="toll_booth"]'],
    "postos": ['nwr["amenity"="fuel"]'],
    "oficinasMoto": ['nwr["shop"~"^(motorcycle|motorcycle_repair)$"]'],
    "estacionamentoMoto": [
        'nwr["amenity"="motorcycle_parking"]',
        'nwr["amenity"="parking"]["motorcycle"="designated"]',
    ],
    "mirantes": ['nwr["tourism"="viewpoint"]'],
    "lombadas": [
        'node["traffic_calming"~"^(' + "|".join(sorted(TIPOS_LOMBADA)) + ')$"]'
    ],
    # Novidade (não existe no app ainda): borracharia é shop=tyres no
    # OpenStreetMap, mas muita gente no Brasil mapeia como oficina de carro
    # (shop=car_repair) com "Borracharia" no nome. Os dois entram.
    "borracharias": [
        'nwr["shop"="tyres"]',
        'nwr["shop"="car_repair"]["name"~"borrach",i]',
    ],
}

# "24h", "24 h", "24hs", "24 horas" no nome (ex.: "Borracharia 24 Horas").
_NOME_24H = re.compile(r"\b24\s*(h|hs|hrs|horas)\b", re.IGNORECASE)

# Os jeitos de escrever "aberto o tempo todo" no opening_hours, já sem
# espaços e em minúsculas.
_HORARIOS_24H = {"24/7", "mo-su00:00-24:00", "00:00-24:00", "mo-su00:00-23:59"}


# ---------------------------------------------------------------------------
# Regras: etiquetas do OpenStreetMap → camada e campos extras
# ---------------------------------------------------------------------------


def texto(tags: dict, chave: str) -> str | None:
    """Devolve o valor da etiqueta [chave] sem espaços nas pontas, ou None
    se ela não existir ou estiver vazia. Ex.: texto({"name": " Shell "},
    "name") → "Shell"."""
    v = tags.get(chave)
    if isinstance(v, str) and v.strip():
        return v.strip()
    return None


def classificar(tags: dict) -> str | None:
    """Diz em qual camada um lugar entra, pelas etiquetas dele (None = em
    nenhuma).

    No OpenStreetMap cada lugar tem etiquetas "chave=valor", como colunas
    de uma tabela: um posto tem amenity=fuel, uma lombada tem
    traffic_calming=bump. A ordem dos testes é a mesma do app (a mais
    específica primeiro): um posto com cancela de pedágio vira pedágio.
    A borracharia entra logo depois das oficinas de moto."""
    amenity = tags.get("amenity")
    shop = tags.get("shop")
    nome = (texto(tags, "name") or "").lower()
    if tags.get("barrier") == "toll_booth":
        return "pedagios"
    if amenity == "fuel":
        return "postos"
    if shop in ("motorcycle", "motorcycle_repair"):
        return "oficinasMoto"
    if shop == "tyres" or (shop == "car_repair" and "borrach" in nome):
        return "borracharias"
    if amenity == "motorcycle_parking" or (
        amenity == "parking" and tags.get("motorcycle") == "designated"
    ):
        return "estacionamentoMoto"
    if tags.get("tourism") == "viewpoint":
        return "mirantes"
    if tags.get("traffic_calming") in TIPOS_LOMBADA:
        return "lombadas"
    return None


def aberto_24h(tags: dict) -> bool:
    """True quando o lugar se declara aberto 24 horas: pelo opening_hours
    (ex.: "24/7") ou pelo nome (ex.: "Borracharia 24 Horas").

    Só existe o "sim": se não houver informação, o arquivo não diz nada, e o
    app também não (nunca mostra "fechado" por falta de dado)."""
    horario = (texto(tags, "opening_hours") or "").lower().replace(" ", "")
    if horario in _HORARIOS_24H:
        return True
    return bool(_NOME_24H.search(texto(tags, "name") or ""))


def numero(valor: str | None) -> float | None:
    """Lê um número escrito de um jeito solto, como aparece no
    OpenStreetMap: "850", "850.5", "850,5" ou "850 m" → 850.0, 850.5...
    Devolve None se não houver número no começo do texto."""
    if not valor:
        return None
    achado = re.match(r"^\s*(-?\d+(?:[.,]\d+)?)", valor)
    if not achado:
        return None
    return float(achado.group(1).replace(",", "."))


def extras(camada: str, tags: dict) -> dict:
    """Os campos a mais de cada camada (além de id, lat, lon e nome).

    O arquivo guarda o DADO, e não a frase pronta: o app monta o texto do
    cartão ("Pago · 20 vagas"). Assim o mesmo arquivo serve se um dia o app
    tiver outro idioma. Só entra no dicionário o que existir."""
    e: dict = {}
    if camada == "pedagios":
        if operador := texto(tags, "operator"):
            e["operador"] = operador
    elif camada == "postos":
        # A bandeira (Shell, Ipiranga...) e quem opera. O app mostra a
        # bandeira e, se não houver, o operador.
        if marca := texto(tags, "brand"):
            e["marca"] = marca
        if operador := texto(tags, "operator"):
            e["operador"] = operador
        if aberto_24h(tags):
            e["h24"] = True
    elif camada == "oficinasMoto":
        # shop=motorcycle é a loja/concessionária (que muitas vezes também
        # faz serviço); motorcycle_repair é a oficina.
        if tags.get("shop") == "motorcycle":
            e["loja"] = True
    elif camada == "estacionamentoMoto":
        vagas = numero(texto(tags, "capacity"))
        if vagas is not None and vagas >= 1:
            e["vagas"] = int(vagas)
        if tags.get("fee") == "yes":
            e["pago"] = True
        elif tags.get("fee") == "no":
            e["pago"] = False
    elif camada == "mirantes":
        altitude = numero(texto(tags, "ele"))
        if altitude is not None:
            e["altitude"] = round(altitude)
    elif camada == "lombadas":
        # Só os tipos que o app descreve: plana (faixa elevada) e com
        # passagem. As comuns não levam nada (são milhares: cada byte conta).
        tipo = tags.get("traffic_calming")
        if tipo in ("table", "cushion"):
            e["tipo"] = tipo
    elif camada == "borracharias":
        if aberto_24h(tags):
            e["h24"] = True
    return e


# ---------------------------------------------------------------------------
# Overpass: a consulta, o download e a leitura da resposta
# ---------------------------------------------------------------------------


def consulta_overpass(uf: str, camada: str) -> str:
    """Monta a pergunta ao Overpass (a "SQL" do OpenStreetMap) para UMA
    camada de UM estado.

    Linha a linha:
      [out:json][timeout:300]    resposta em JSON; o servidor desiste em 300 s
      area["ISO3166-2"="BR-PE"]  a área do estado, guardada no nome .uf
      ( ... (area.uf); )         os filtros da camada, só dentro do estado
      out center tags qt;        etiquetas e, das áreas, o ponto central
                                 (é onde o ícone fica); qt = ordem mais rápida
    """
    filtros = "\n".join(f"  {f}(area.uf);" for f in CAMADAS[camada])
    return (
        f"[out:json][timeout:{TEMPO_SERVIDOR}];\n"
        f'area["ISO3166-2"="BR-{uf}"]->.uf;\n'
        f"(\n{filtros}\n);\n"
        "out center tags qt;"
    )


def baixar_overpass(consulta: str) -> dict:
    """Manda a consulta ao Overpass e devolve a resposta já lida (dict).

    Tenta o servidor principal e, se falhar, o espelho. Se os dois falharem,
    espera [ESPERA_ENTRE_VOLTAS] segundos e dá outra volta, até [VOLTAS]
    voltas. Desistiu: lança RuntimeError com o último erro.

    O envio é um POST de formulário com a consulta no campo "data" (o mesmo
    formato que o app usa)."""
    corpo = urllib.parse.urlencode({"data": consulta}).encode("utf-8")
    ultimo_erro: object = "nenhum servidor"
    for volta in range(1, VOLTAS + 1):
        for endereco in SERVIDORES:
            pedido = urllib.request.Request(
                endereco,
                data=corpo,
                headers={
                    "User-Agent": IDENTIFICACAO,
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
            try:
                with urllib.request.urlopen(pedido, timeout=TEMPO_HTTP) as r:
                    return json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as erro:
                # 429 = muitos pedidos; 504 = servidor ocupado.
                ultimo_erro = f"HTTP {erro.code} em {endereco}"
            except (urllib.error.URLError, TimeoutError, OSError) as erro:
                ultimo_erro = f"{erro} em {endereco}"
            except json.JSONDecodeError:
                ultimo_erro = f"resposta que não é JSON em {endereco}"
            print(f"      falhou ({ultimo_erro})", flush=True)
        if volta < VOLTAS:
            print(f"      esperando {ESPERA_ENTRE_VOLTAS} s para tentar de novo",
                  flush=True)
            time.sleep(ESPERA_ENTRE_VOLTAS)
    raise RuntimeError(f"Overpass indisponível: {ultimo_erro}")


def ler_resposta(json_resposta: dict) -> list[dict]:
    """Transforma a resposta do Overpass na lista de lugares do arquivo.

    Cada lugar sai assim (só o que existir):
        {"id": "n123", "lat": -8.05123, "lon": -34.91234, "nome": "...",
         ...campos extras da camada...}
    e com a chave interna "_camada" (tirada na hora de gravar).

    - id: "n" (ponto), "w" (área/linha) ou "r" (conjunto) + o número do
      OpenStreetMap, igual ao app;
    - lat/lon: do ponto, ou o centro da área; 5 casas (cerca de 1 m);
    - lugares sem posição ou de nenhuma camada ficam de fora.

    Se o servidor avisar de erro no meio (o "remark", como "runtime error:
    Query timed out"), a resposta veio PELA METADE: lança ValueError, para
    nada disso ser gravado."""
    aviso = json_resposta.get("remark")
    if isinstance(aviso, str) and "error" in aviso.lower():
        raise ValueError(f"Overpass: {aviso}")
    elementos = json_resposta.get("elements")
    if not isinstance(elementos, list):
        raise ValueError("Resposta sem a lista 'elements'")

    lugares = []
    for el in elementos:
        tags = el.get("tags")
        if not isinstance(tags, dict) or not isinstance(el.get("id"), int):
            continue
        # O ponto: o lat/lon do node, ou o "center" da área.
        fonte = el if el.get("type") == "node" else el.get("center")
        if not isinstance(fonte, dict):
            continue
        lat, lon = fonte.get("lat"), fonte.get("lon")
        if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
            continue
        camada = classificar(tags)
        if camada is None:
            continue
        letra = {"node": "n", "way": "w"}.get(el.get("type"), "r")
        lugar = {
            "_camada": camada,
            "id": f"{letra}{el['id']}",
            "lat": round(lat, 5),
            "lon": round(lon, 5),
        }
        if nome := texto(tags, "name"):
            lugar["nome"] = nome
        lugar.update(extras(camada, tags))
        lugares.append(lugar)
    return lugares


# ---------------------------------------------------------------------------
# Montar, conferir e gravar o arquivo do estado
# ---------------------------------------------------------------------------


def chave_ordem(lugar: dict) -> tuple:
    """A ordem dos lugares dentro de cada camada: pelo tipo (n, r, w) e pelo
    número do id. Ordem fixa = o mesmo conteúdo gera exatamente o mesmo
    arquivo, e o sha256 só muda quando os DADOS mudam."""
    return (lugar["id"][0], int(lugar["id"][1:]))


def separar_por_camada(lugares: list[dict]) -> dict[str, list[dict]]:
    """Junta os lugares de todas as consultas num dicionário camada → lista
    (como um groupby), sem repetidos e já em ordem.

    Repetido acontece: um posto com cancela aparece na consulta dos postos e
    na dos pedágios, mas a classificar() manda os dois para o mesmo lugar.
    Fica uma cópia só, pelo id."""
    por_camada: dict[str, dict[str, dict]] = {c: {} for c in CAMADAS}
    for lugar in lugares:
        camada = lugar["_camada"]
        limpo = {k: v for k, v in lugar.items() if k != "_camada"}
        por_camada[camada][limpo["id"]] = limpo
    return {
        c: sorted(itens.values(), key=chave_ordem)
        for c, itens in por_camada.items()
    }


def montar_texto(uf: str, por_camada: dict[str, list[dict]]) -> str:
    """Escreve o conteúdo do arquivo do estado.

    É JSON normal, mas com UM LUGAR POR LINHA: no GitHub, a comparação entre
    um mês e outro mostra exatamente o que entrou e o que saiu (ex.: "+ um
    posto em Caruaru"), em vez de uma linha gigante trocada. Não tem data
    dentro (ela fica no indice.json): se nada mudou no estado, o arquivo sai
    idêntico, byte a byte."""
    def linha(obj) -> str:
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))

    blocos = []
    for camada in CAMADAS:
        itens = por_camada.get(camada, [])
        if itens:
            corpo = ",\n".join(linha(i) for i in itens)
            blocos.append(f"{linha(camada)}:[\n{corpo}\n]")
        else:
            blocos.append(f"{linha(camada)}:[]")
    cabecalho = (
        f'{{"formato":{FORMATO},"uf":{linha(uf)},'
        f'"fonte":{linha(FONTE)},"licenca":{linha(LICENCA)},'
        '"camadas":{\n'
    )
    return cabecalho + ",\n".join(blocos) + "\n}}\n"


def contar(por_camada: dict[str, list[dict]]) -> dict[str, int]:
    """Quantos lugares há em cada camada (para o resumo e para a trava)."""
    return {c: len(por_camada.get(c, [])) for c in CAMADAS}


def ler_contagem_anterior(arquivo: Path) -> dict[str, int] | None:
    """A contagem por camada da versão que já está gravada (None se ainda
    não existe ou se o arquivo não puder ser lido)."""
    if not arquivo.exists():
        return None
    try:
        dados = json.loads(arquivo.read_text(encoding="utf-8"))
        return {c: len(dados["camadas"].get(c, [])) for c in CAMADAS}
    except (OSError, ValueError, KeyError, AttributeError):
        return None


def problemas(novo: dict[str, int], anterior: dict[str, int] | None) -> list[str]:
    """A TRAVA: a lista de motivos para NÃO gravar o estado (vazia = ok).

    1) Nenhum posto: todo estado tem posto mapeado. Zero quer dizer que a
       área do estado não foi achada ou que a resposta veio vazia.
    2) Uma camada com mais de 30% a menos do que na versão anterior (só
       para camadas com pelo menos 20 lugares antes): provável resposta
       pela metade. Se a queda for real (uma limpeza no OpenStreetMap,
       por exemplo), dá para gravar com --forcar."""
    motivos = []
    if novo.get("postos", 0) == 0:
        motivos.append("nenhum posto (área do estado não achada ou resposta vazia)")
    if anterior:
        for camada, antes in anterior.items():
            agora = novo.get(camada, 0)
            if antes >= MINIMO_PARA_COMPARAR and agora < antes * (1 - QUEDA_MAXIMA):
                motivos.append(f"{camada} caiu de {antes} para {agora}")
    return motivos


def gravar(arquivo: Path, conteudo: str) -> None:
    """Grava o arquivo de um jeito seguro: escreve primeiro num .tmp e só
    então troca pelo definitivo. Se o script parar no meio (queda de luz,
    Ctrl+C), o arquivo antigo continua inteiro."""
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    temporario = arquivo.with_suffix(".json.tmp")
    temporario.write_text(conteudo, encoding="utf-8")
    temporario.replace(arquivo)


def gerar_uf(
    uf: str,
    pasta: Path = PASTA_CAMADAS,
    forcar: bool = False,
    baixar: Callable[[str], dict] = baixar_overpass,
    pausa: float = PAUSA_ENTRE_CONSULTAS,
) -> bool:
    """Gera (ou atualiza) o arquivo de UM estado. Devolve True se gravou ou
    se não havia nada novo; False se falhou ou se a trava segurou.

    Passos: uma consulta por camada (com pausa entre elas) → junta e separa
    por camada → confere com a versão anterior (a trava) → grava → imprime
    o resumo. [baixar] e [pausa] podem ser trocados nos testes, para rodar
    sem internet e sem esperar."""
    print(f"\n{uf}", flush=True)
    lugares: list[dict] = []
    try:
        for i, camada in enumerate(CAMADAS):
            if i > 0:
                time.sleep(pausa)
            print(f"   {camada}…", flush=True)
            lugares.extend(ler_resposta(baixar(consulta_overpass(uf, camada))))
    except (RuntimeError, ValueError) as erro:
        print(f"   ✗ {uf} não gravado: {erro}", flush=True)
        return False

    por_camada = separar_por_camada(lugares)
    novo = contar(por_camada)
    arquivo = pasta / f"{uf}.json"
    anterior = ler_contagem_anterior(arquivo)

    print(f"   {'camada':<20}{'antes':>8}{'agora':>8}", flush=True)
    for camada in CAMADAS:
        antes = "-" if anterior is None else anterior.get(camada, 0)
        print(f"   {camada:<20}{antes:>8}{novo[camada]:>8}", flush=True)

    motivos = problemas(novo, anterior)
    if motivos and not forcar:
        for m in motivos:
            print(f"   ✗ trava: {m}", flush=True)
        print(f"   ✗ {uf} não gravado (confira e, se estiver certo, rode com "
              "--forcar)", flush=True)
        return False

    conteudo = montar_texto(uf, por_camada)
    if arquivo.exists() and arquivo.read_text(encoding="utf-8") == conteudo:
        print(f"   = {uf} sem mudanças", flush=True)
        return True
    gravar(arquivo, conteudo)
    # Tamanho em KB com ponto de milhar, do jeito brasileiro (1.234 KB).
    kb = f"{len(conteudo.encode('utf-8')) / 1024:,.0f}".replace(",", ".")
    print(f"   ✓ {arquivo.relative_to(pasta.parent)} gravado ({kb} KB)",
          flush=True)
    return True


def principal(argumentos: list[str] | None = None) -> int:
    """Lê a linha de comando e gera os estados pedidos. Devolve o código de
    saída: 0 = tudo certo; 1 = algum estado falhou (os outros foram
    gravados mesmo assim). O código 1 é o que faz a tarefa mensal do
    GitHub, no futuro, avisar por e-mail que algo deu errado."""
    leitor = argparse.ArgumentParser(
        description="Gera camadas/UF.json a partir do OpenStreetMap.")
    leitor.add_argument("ufs", nargs="*", help="siglas dos estados (ex.: PE PB)")
    leitor.add_argument("--todos", action="store_true", help="os 27 estados")
    leitor.add_argument("--forcar", action="store_true",
                        help="grava mesmo se a trava apontar queda grande")
    args = leitor.parse_args(argumentos)

    ufs = UFS if args.todos else [u.upper() for u in args.ufs]
    invalidas = [u for u in ufs if u not in UFS]
    if not ufs or invalidas:
        leitor.error(f"estados inválidos: {invalidas}" if invalidas
                     else "diga os estados (ex.: PE) ou use --todos")

    falharam = []
    for i, uf in enumerate(ufs):
        if i > 0:
            time.sleep(PAUSA_ENTRE_CONSULTAS)
        if not gerar_uf(uf, forcar=args.forcar):
            falharam.append(uf)

    print()
    if falharam:
        print(f"Falharam: {', '.join(falharam)} (a versão anterior deles "
              "continua valendo).")
        return 1
    print("Pronto. Agora rode: python3 scripts/atualizar_indice.py")
    return 0


if __name__ == "__main__":
    sys.exit(principal())
