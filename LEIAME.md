# Dados públicos do Rodando Pelo Mundo (RPM)

Arquivos públicos, só de leitura, usados pelo app **Rodando Pelo Mundo
(RPM)**, de passeios de moto. Ficam em
<https://dados.rodandopelomundo.com.br>.

Nada aqui é dado de usuário: são informações públicas, como os pontos do
mapa vindos do OpenStreetMap.

## Arquivos

| Arquivo | O que é |
| --- | --- |
| `indice.json` | O que existe e em que versão. O app lê este primeiro e só baixa o que mudou. |
| `camadas/UF.json` | As camadas do mapa de um estado (`camadas/PE.json`, `camadas/SP.json`...). |
| `scripts/` | Os scripts em Python que geram os arquivos. |
| `LICENCA.md` | Licenças e créditos. |

## `indice.json`

```json
{
  "formato": 1,
  "gerado_em": "2026-11-06T20:00:00-03:00",
  "arquivos": {
    "camadas/PE.json": {
      "versao": 202611062000,
      "bytes": 812345,
      "sha256": "…",
      "bbox": [-9.483, -41.359, -3.832, -32.391]
    }
  }
}
```

- `versao`: data e hora da última mudança do arquivo (AAAAMMDDHHMM, horário
  de Brasília). Só sobe quando o conteúdo muda.
- `bytes` e `sha256`: do arquivo descomprimido (o servidor entrega
  comprimido com gzip).
- `bbox`: o retângulo `[sul, oeste, norte, leste]` que cobre todos os pontos
  do arquivo.

## `camadas/UF.json`

Um ponto por linha, agrupados por camada:

| Camada | Do OpenStreetMap | Campos extras |
| --- | --- | --- |
| `pedagios` | `barrier=toll_booth` | `operador` |
| `postos` | `amenity=fuel` | `marca`, `operador`, `h24` |
| `oficinasMoto` | `shop=motorcycle` ou `motorcycle_repair` | `loja` (concessionária ou loja) |
| `estacionamentoMoto` | `amenity=motorcycle_parking`, ou estacionamento com `motorcycle=designated` | `vagas`, `pago` |
| `mirantes` | `tourism=viewpoint` | `altitude` (m) |
| `lombadas` | `traffic_calming` = bump, hump, table, cushion ou yes (só pontos) | `tipo` (só `table` ou `cushion`) |
| `borracharias` | `shop=tyres`, ou `shop=car_repair` com "borrach" no nome | `h24` |

Todo ponto tem `id` (`n` = ponto, `w` = área, `r` = conjunto, mais o número
no OpenStreetMap), `lat` e `lon` (5 casas, cerca de 1 m) e, quando houver,
`nome`. As áreas viram o ponto central delas. `h24` só aparece quando o
lugar se declara aberto 24 horas (no horário ou no nome); sem informação,
o campo não existe.

## Atualização

Uma vez por mês. Viu um posto que fechou, uma lombada que não existe ou uma
borracharia faltando? Corrija direto no
[OpenStreetMap](https://www.openstreetmap.org): a correção entra aqui na
atualização seguinte.

## Créditos

Camadas do mapa: **© colaboradores do OpenStreetMap**, sob a ODbL 1.0
(veja `LICENCA.md`).

Contato: approdandopelomundo@gmail.com · <https://rodandopelomundo.com.br>
