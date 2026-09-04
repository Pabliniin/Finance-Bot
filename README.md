# Bot Finanzas — sistema de apoyo a la decisión (forex)

Bot de Discord que analiza forex con reglas explícitas y reproducibles, opera
en **paper trading** (simulado) por defecto, y publica su propia tasa de
acierto real — incluida cuando es mala. **No ejecuta operaciones reales, no
promete rentabilidad y no es asesoramiento financiero.** La operativa con
dinero real es siempre manual y tuya.

Si el sistema no encuentra ventaja estadística real, está diseñado para
decírtelo y dejar de emitir señales, no para disimularlo.

## 0. Antes de nada: qué necesitas

- Un PC con **Windows** siempre encendido (el mini PC del que hablamos) — es
  el único sitio donde puede correr el conector a MetaTrader 5.
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) instalado
  en ese mismo PC.
- [Python 3.11+](https://www.python.org/downloads/) instalado en el PC
  (independiente de Docker — lo necesita solo la pieza que habla con MT5).
- Una cuenta **demo** en XM con la app **MetaTrader 5** instalada y
  funcionando en ese PC.
- Una aplicación de bot de Discord ya creada (nos dijiste que la tienes) con
  su token, y un servidor de Discord donde invitarla.

Nada de esto cuesta dinero. Todas las fuentes de datos usadas son gratuitas.

## 1. Instalación paso a paso

### 1.1 Copiar el proyecto

Asegúrate de tener esta carpeta completa (`Bot Finanzas/`) en el mini PC
Windows, por ejemplo en `C:\BotFinanzas`.

### 1.2 Configurar las variables de entorno

```bash
copy .env.example .env
```

Abre `.env` con un editor de texto y rellena:

- `POSTGRES_PASSWORD`: invéntate una contraseña (solo la usa este sistema,
  no necesitas recordarla).
- `DISCORD_BOT_TOKEN`: el token de tu aplicación de Discord (Discord
  Developer Portal → tu app → Bot → Reset Token).
- `DISCORD_GUILD_ID`: el ID de tu servidor de Discord (clic derecho sobre el
  icono del servidor con el modo desarrollador activado → "Copiar ID").
- `DISCORD_ALERTS_CHANNEL_ID` / `DISCORD_REPORTS_CHANNEL_ID`: IDs de los
  canales donde quieres alertas de señales/riesgo y el informe diario
  respectivamente (pueden ser el mismo canal).
- `MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER`: los datos de tu cuenta demo de
  XM (el servidor suele ser algo como `XMGlobal-Demo`).

No hace falta tocar `DATABASE_URL` ni `DATABASE_URL_HOST` — ya están
preparados para funcionar con `docker-compose.yml` tal cual.

### 1.3 Invitar el bot a tu servidor

En el Discord Developer Portal, genera una URL de invitación (OAuth2 → URL
Generator) con los scopes `bot` y `applications.commands`, y los permisos
"Send Messages", "Embed Links" y "Use Slash Commands". Ábrela y añade el bot
a tu servidor.

### 1.4 Preparar el entorno Python nativo (para hablar con MT5)

MetaTrader 5 solo puede controlarse desde Windows de forma nativa — Docker no
puede acceder a él directamente. Por eso una pequeña pieza (el "collector")
corre fuera de Docker:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[windows-collector]"
```

### 1.5 Levantar la base de datos y el resto del sistema con Docker

```bash
docker compose up -d db
```

Espera unos segundos a que la base de datos esté lista, y aplica el esquema:

```bash
.venv\Scripts\activate
python -m data.storage.migrate
```

Ahora levanta el bot y el planificador:

```bash
docker compose up -d --build bot scheduler
```

### 1.6 Cargar histórico y datos en vivo

Con MetaTrader 5 abierto y la sesión demo iniciada:

```bash
.venv\Scripts\activate
python -m collector.mt5_live_collector
```

La primera vez tardará un buen rato: descarga el histórico de los 30
instrumentos en las 3 temporalidades configuradas. Revisa `collector_mt5.log`
si algo falla.

### 1.7 Automatizar el collector con el Programador de tareas de Windows

El bot necesita datos frescos cada hora. Abre el **Programador de tareas** de
Windows y crea una tarea:

- Desencadenador: repetir cada 1 hora, indefinidamente.
- Acción: ejecutar `C:\BotFinanzas\.venv\Scripts\python.exe` con el argumento
  `-m collector.mt5_live_collector`, con "Iniciar en" apuntando a
  `C:\BotFinanzas`.
- Marca "Ejecutar tanto si el usuario inició sesión como si no" y asegúrate
  de que MetaTrader 5 esté configurado para arrancar solo al iniciar Windows.

### 1.8 Verificar que todo funciona

En Discord, prueba:

```
/estado
```

Deberías ver el estado de las últimas ingestas y si hay instrumentos con
datos obsoletos. Si algo falla, este comando es tu primera parada.

## 2. Ejecutar la validación (Fase 3)

**Antes de fiarte de ninguna señal**, hay que comprobar si las estrategias
tienen de verdad ventaja estadística fuera de muestra. Con el histórico ya
cargado (paso 1.6):

```bash
.venv\Scripts\activate
pip install -e ".[dev]"
python -m scripts.run_full_backtest
```

Esto puede tardar bastante (recorre 30 instrumentos × 3 estrategias, con
walk-forward, 200 simulaciones aleatorias y 2000 iteraciones de Monte Carlo
por combinación). Para iterar más rápido durante pruebas:

```bash
python -m scripts.run_full_backtest --instruments EURUSD,GBPUSD --strategies trend_following
```

El resultado se guarda en `reports/backtest_summary.json` y ya lo puedes
consultar en Discord con `/backtest <estrategia>`. **Si una estrategia sale
descartada, ese es el resultado — no se relanza el script con otros
parámetros hasta encontrar algo bonito.** Eso sería exactamente el
sobreajuste que este proyecto existe para evitar.

Si decides seguir adelante con alguna estrategia, el propio script te
sugiere un valor para activar el kill switch de drawdown:

```yaml
# config/config.yaml
backtest:
  reference_max_drawdown_pct: -18.5   # ejemplo: el peor DD visto en el backtest
```

Sin este valor, el kill switch de drawdown queda inactivo (los límites de
pérdida diaria/semanal SÍ funcionan siempre, sin depender de esto).

## 3. Uso diario

Con todo levantado, el `scheduler` corre solo: recoge calendario y noticias,
genera señales, aplica los límites de riesgo, abre/cierra posiciones de papel
y publica un informe diario en el canal configurado, además de alertas cuando
salta una señal o un evento de riesgo (kill switch).

Comandos disponibles en Discord:

| Comando | Qué hace |
|---|---|
| `/senales` | Posiciones abiertas ahora mismo en paper trading |
| `/stats [periodo]` | Rendimiento real (win rate, expectativa, drawdown) — 7d/30d/90d/all |
| `/analisis <ticker>` | Ficha técnica + volatilidad + noticias de un instrumento |
| `/riesgo <ticker> <stop>` | Calculadora de tamaño de posición por riesgo fijo |
| `/backtest <estrategia>` | Último resultado de validación fuera de muestra |
| `/noticias [ticker]` | Titulares recientes con sentimiento y fuente |
| `/estado` | Salud del sistema: última ingesta, errores, datos obsoletos |

## 4. Parar / reiniciar

```bash
docker compose down          # para bot + scheduler + base de datos
docker compose up -d         # los vuelve a levantar (los datos persisten)
```

El collector nativo se detiene/reactiva desde el Programador de tareas de
Windows.

## 5. Si algo va mal

- **El bot no responde a los slash commands**: revisa `docker compose logs
  bot`. Si acabas de invitarlo, los comandos globales pueden tardar hasta 1h
  en propagarse (se sincronizan al instante si configuraste
  `DISCORD_GUILD_ID`).
- **`/estado` muestra instrumentos obsoletos**: el collector nativo no está
  corriendo o MT5 no tiene sesión iniciada — revisa `collector_mt5.log`.
- **El kill switch se activó**: es intencional y no se reactiva solo. Revisa
  el motivo con `/estado`, entiende qué pasó, y si decides reactivarlo hazlo
  explícitamente:
  ```python
  python -c "from risk.kill_switch import manual_reset; from data.storage.db import get_engine; from config.settings import get_settings; manual_reset(get_engine(get_settings().database_url_host), 'revisado manualmente el 2026-XX-XX')"
  ```
- **`duka` o el feed de calendario fallan**: son dos integraciones externas
  no oficiales marcadas explícitamente en el código
  (`data/providers/dukascopy_provider.py`, `data/providers/calendar_provider.py`)
  como pendientes de verificar la primera vez que se ejecutan de verdad.

## 6. Límites que debes conocer

- El backtest usa datos de Dukascopy; el paper trading en vivo usa los de tu
  broker (XM) vía MT5 — hay una diferencia de precios entre ambos, pequeña
  pero real.
- El motor de backtest no comparte capital entre instrumentos simultáneos al
  validar cada combinación por separado (sí lo hace correctamente en
  producción, vía `risk/limits.py`) — ver la nota en
  `scripts/run_full_backtest.py::_aggregate_metrics`.
- La conversión de P&L a EUR usa el precio más reciente en caché del cruce
  EUR correspondiente, no el histórico exacto de cada operación.

Ninguno de estos puntos está escondido: están comentados en el código exacto
donde importan, para que si algún día quieres mejorarlos sepas dónde mirar.
