# relatorios_ubidots

App web local (FastAPI + Python embarcado) para consultar dados historicos e gerar relatorios PDF/CSV de **duas plataformas IoT distintas**, com preview na UI e sistema de transformacoes de unidade.

## Arquitetura

```
relatorios_ubidots/
├── python_runtime/         # Python 3.12.7 embeddable (auto-contido, NAO mexer)
├── app/
│   ├── main.py             # FastAPI: /, /api/groups, /api/devices, /api/variables, /api/preview, /relatorio
│   ├── config.py           # Settings via pydantic-settings (.env). _strip_api_suffix aceita URLs com path/query
│   ├── transforms.py       # Mapa (platform, variable) -> {factor, unit, decimals} - PONTO DE EXTENSAO
│   ├── clients/
│   │   ├── __init__.py     # make_client(platform_id) -> PlatformClient (Protocol)
│   │   ├── ubidots.py      # UbidotsClient: X-Auth-Token, v2.0 (groups, paginacao), v1.6 (historico)
│   │   └── nexus.py        # NexusCoreClient: Bearer ag_..., /api/devices array, variaveis embutidas
│   ├── reports/
│   │   ├── csv_report.py   # UTF-8 BOM, ; separator, colunas ctx.<chave> separadas + valor_convertido
│   │   └── pdf_report.py   # reportlab, landscape se tem context, formato BR para numeros
│   ├── templates/index.html
│   └── static/style.css
├── output/
├── .env                    # tokens (NAO commitar)
├── .env.example
├── requirements.txt
└── run.bat                 # duplo-clique sobe servidor em 127.0.0.1:8000
```

## Plataformas suportadas

### Ubidots (industrial.api.ubidots.com)
- Auth: header `X-Auth-Token: BBUS-...`
- API v2.0: `/devices/`, `/device_groups/`, `/variables/?device__deviceGroup__label=...` (paginacao via `next`)
- API v1.6: `/devices/<label>/<var>/values?start=<ms>&end=<ms>` (historico)
- Resposta: `{count, next, previous, results: [...]}`
- Grupos podem retornar 403 se o token nao tiver permissao -> client retorna `[]` (UI deixa digitar manual)

### JKControl (jkcontrol.online) - NEXUS CORE
- **NAO eh Ubidots** apesar de usar `v1.6/...` nos topicos MQTT
- Auth: header `Authorization: Bearer ag_...`
- Base REST: `/api` (sem prefixo de versao)
- `GET /api/devices` retorna ARRAY direto com variaveis **embutidas** (`{label, name, variables: [{label, unit, data: [...]}]}`)
- Historico: `GET /api/devices/:label/variables/:var/data?startDate=&endDate=&limit=` -> array `[{_id, timestamp(ISO), value, context}]`
- Grupos: campo string `group` no proprio device (derivado localmente, sem endpoint dedicado)
- Docs completas: `C:\Users\JKControl\Desktop\PROJETOS IA\projeto Antigravitiy emqx Multitenancy\API_DOCS.md`

## Sistema de transformacoes (app/transforms.py)

Ponto de extensao quando o valor armazenado nao esta na unidade desejada. Mapa `(platform_id, variable_label) -> Transform`:

```python
TRANSFORMS = {
    ("ubidots", "ciclo"): Transform(factor=1/1000, unit="s", decimals=3,
                                    description="ciclo gravado em ms, exibir em segundos"),
}
```

Aplicado em preview, CSV (coluna `valor_convertido`) e PDF (cabecalho "Valor (unit)"). Numeros saem em formato BR (`71,296` com virgula decimal).

## Como rodar

1. Duplo-clique em `run.bat` (cria `.env` na 1a vez)
2. Edite `.env` com os 2 tokens
3. http://127.0.0.1:8000
4. Fluxo: Plataforma -> Grupo (opcional) -> Dispositivo -> Variavel -> Datas -> **Visualizar** -> **Baixar PDF/CSV**

## Pontos de atencao

- **Token Ubidots Industrial** pode nao ter permissao para `/device_groups/` (403) - graceful fallback para listagem vazia, usuario digita manualmente
- **JKControl /api/analytics/csv/** documentado mas pode retornar 404 no deploy atual - geramos CSV nos mesmo
- **tzdata** eh dependencia obrigatoria no Windows (zoneinfo nao tem dados nativos)
- **Python embarcado** usa `Lib\site-packages` via `python312._pth` (NAO usar venv)
- Para adicionar 3a plataforma: novo cliente em `app/clients/`, registrar em `PLATFORMS` e em `make_client()`
