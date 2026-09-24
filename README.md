# Finance Bot — señales XAUUSD / EURUSD por Discord

Bot de Discord que analiza **XAUUSD (oro)** y **EURUSD** en **M1, M15, H1, H4 y D1** con
20 estrategias que votan y 8 estrategias de entrada, y estima con un modelo
calibrado **fuera de muestra** la probabilidad real de que cada operación llegue
a su objetivo antes que al stop. Te dice la temporalidad, la duración esperada,
todo lo que va a favor y en contra y cuánto arriesgar con tu capital.

**Lo que NO hace:** no opera por ti (no tiene acceso de trading a ninguna
cuenta), no promete rentabilidad y no es asesoramiento financiero.

> **Resultado honesto de la validación: ver la sección 2 antes que nada.**

---

## 1. Cómo funciona (en una página)

Cada minuto (con MetaTrader 5; cada 3 con el respaldo gratuito), sobre velas
**ya cerradas** (nunca la vela en formación):

1. **20 estrategias votan** (+1 compra / −1 venta / 0 sin opinión), agrupadas en familias:
   - tendencia: EMAs 20/50/200, pendiente de la EMA200, Supertrend, ADX/DMI, Ichimoku, estructura de máximos y mínimos, canal Donchian
   - momentum: MACD, régimen de RSI, Stoch RSI, impulso
   - niveles y reversión: Bollinger, soportes/resistencias, pivote diario, Fibonacci 38–62%
   - volatilidad: squeeze Bollinger/Keltner
   - temporalidades superiores: H1 → H4 → D1
   - intermercado: el otro instrumento como termómetro del dólar
   - patrones de vela
2. **8 estrategias de entrada** detectan el momento concreto:
   - pullback a la EMA20
   - ruptura Donchian con ADX
   - cruce de MACD
   - pullback de RSI
   - reversión en Bollinger
   - ruptura de estructura
   - salida de compresión
   - giro de Supertrend
3. Un **modelo de probabilidad** (regresión logística, walk-forward con purga, calibración de Platt) convierte esa confluencia en «probabilidad de tocar TP1 antes que el stop». No cuenta votos: aprende de 14 años de historia cuánto aporta *de verdad* cada estrategia, incluido que muchas dicen lo mismo.
4. **Casos similares**: de todas las señales pasadas fuera de muestra con probabilidad parecida, el bot calcula:
   - la escalera de objetivos (qué % llegó a +0,5R, +1R, +1,5R, +2R, +3R antes del stop)
   - la expectativa en R después de costes
   - cuánto duraron
5. **Puertas de emisión**. En modo estricto solo te avisa si se cumple todo:
   - la combinación instrumento/temporalidad superó la validación
   - la probabilidad está sobre el umbral validado
   - la expectativa de los casos similares es positiva
   - hay suficientes casos similares
   - no hay noticia de alto impacto USD/EUR encima (intradía)
   - el interruptor de seguridad está apagado
   - no se supera el máximo de señales abiertas

   En modo **informativo** (el que viene configurado) te envía los setups que disparan, cada uno con sus avisos: «SIN VENTAJA VALIDADA», expectativa histórica real, umbral no alcanzado… Sigue bloqueando en ambos modos lo que hace la operación inejecutable o peligrosa: que llegue tarde, que haya una noticia fuerte encima, que **el spread esté ahora mucho más ancho de lo normal** (mercado ilíquido) o que **el coste se lleve todo tu riesgo**. Además marca avisos cuando el coste es alto (típico en M1), cuando hay un nivel antes del TP1 o cuando el modelo y los casos reales parecidos discrepan mucho.

   Cuando varias temporalidades disparan a la vez, se envía **solo la mejor de cada instrumento**: la de mayor expectativa histórica (ponderada por cuántos casos la respaldan), no simplemente la más probable.
6. **Seguimiento**: cada señal enviada se sigue hasta el cierre con las mismas reglas del backtest. Recibes un aviso cuando toca TP1 (cierra la mitad y mueve el stop a la entrada), TP2, stop o cierre por tiempo. `/stats` compara lo real con lo que predijo el modelo y, con muestra suficiente, te dice si los aciertos van **en línea, por encima o por debajo** de lo previsto.
7. **Avisos de cierre**: mientras la operación vive, el bot te avisa si ve motivo para salir antes:
   - el contexto de esa temporalidad se gira en contra (6 o más votos netos)
   - dispara una entrada en sentido contrario
   - hay noticia de alto impacto encima
   - se acaba el tiempo del plan
   - estabas en +1R o más y has devuelto 0,6R

   Estos avisos **no** forman parte de lo validado: el backtest mantiene hasta stop, objetivo o tiempo. Por eso cada aviso se guarda con el R que llevabas y, al cerrar, el bot te enseña si cerrar ahí habría sido mejor o peor que seguir el plan. Con el tiempo, `/stats` te dirá si estos avisos suman o restan.

**Costes incluidos en todo** (backtest y seguimiento):

- spread de cuenta XM Standard: oro ≈ 0,016% del precio, EURUSD 1,4 pips
- deslizamiento, incluidos los huecos del fin de semana en los stops
- swap por noche, triple los miércoles

Si stop y objetivo caben en la misma vela, se asume el stop (pesimista).

---

## 2. Resultado de la validación (lee esto primero)

Validación hecha el 22/09/2026 con datos reales de Dukascopy. El informe completo,
con todas las tablas, está en [`docs/validacion_2026-09-22.md`](docs/validacion_2026-09-22.md).

**Cómo se hizo**
- 89.502 setups históricos con su resultado real. Cada uno lo puntuó un modelo que no había visto ese periodo.
- 2 instrumentos × 4 temporalidades × 3 planes de salida fijados de antemano: equilibrado (1R/2R), alto acierto (0,5R/1R) y tendencia (1,5R/3R).
- Validación 2015–2021 (M15: julio 2021–2023). Con ella se eligieron los umbrales.
- Test reservado 2022–2026 (M15: 2024–2026). Se evaluó **una sola vez**, con todo ya congelado.
- Corrección por comparaciones múltiples entre todas las combinaciones contrastadas.

**Lo que salió, sin maquillaje:**

1. **Ninguna combinación tiene ventaja estadística después de costes.** Por eso, en modo estricto, el bot no te envía señales de entrada: te enseña todo lo que ve, pero no te dice que entres.
2. **El caso más prometedor no aguantó el test.**
   - En validación, el oro en H1 batía con claridad a las entradas aleatorias (p < 0,001): +0,09R por operación con el plan equilibrado y +0,20R con el de tendencia.
   - Aun así, ni siquiera ahí se distinguía de cero con seguridad (p = 0,07 y 0,05).
   - En el test reservado dio **-0,19R** (119 operaciones) y **-0,02R** (73 operaciones). No era una ventaja real.
3. **Acertar mucho no es ganar.**
   - El plan de alto acierto pone el objetivo a 0,5R y el stop a 1R, así que hace falta acertar más del 67% solo para empatar antes de costes.
   - Sin filtro acierta el 58% y pierde -0,09R por operación.
   - Con el filtro del modelo sube al 63% de acierto y aun así se queda en -0,02R.
4. **M15 es la temporalidad que peor sale.** Con stops cortos, el spread pesa mucho más en cada operación. Sin filtrar pierde entre -0,06R y -0,18R por operación, según instrumento, plan y periodo.
5. **Las probabilidades son realistas en la zona central y optimistas en la alta.**
   - En el test, cuando el modelo dice 57,5% ocurre el 58,5% (18.400 casos), y cuando dice 61,7% ocurre el 62,4% (8.644 casos).
   - En la zona alta, que es justo donde saltaría una señal, sobrestima: en el plan equilibrado, cuando dijo 51,5% ocurrió el 45,3% (792 casos).
   - Por eso cada señal enseña, al lado de la probabilidad del modelo, lo que ocurrió de verdad en los casos parecidos.
   - El modelo distingue algo (AUC 0,55–0,58), pero no lo suficiente para superar los costes.

**Qué significa para ti.** El bot es un analista honesto: te dice qué ve, qué suele pasar
en situaciones parecidas y por qué no recomienda entrar. No es una máquina de señales
ganadoras, porque esa máquina no ha aparecido en 14 años de datos con estas estrategias
clásicas.

Si quieres ver los setups igualmente, `/modo informativo` te los manda marcados con
⚠️ **SIN VENTAJA VALIDADA** y con su expectativa histórica real. El bot los sigue en papel
hasta el cierre y `/stats` te enseña cómo van. El test histórico ya está gastado, así
que ese seguimiento en vivo es la única prueba limpia que queda (ver sección 5).

---

## 3. Puesta en marcha (30 minutos, casi todo esperando)

### 3.1 Crear el bot en Discord (3 minutos)
1. Entra en <https://discord.com/developers/applications> → **New Application** → ponle nombre.
2. Pestaña **Bot** → **Reset Token** → **Copy**. Ese token es la llave: no lo compartas ni lo subas a ningún sitio.
3. En esa misma pestaña, **desactiva "Public Bot"**. Así solo tú puedes meterlo en un servidor.
4. No hace falta activar ningún *intent* ni permiso especial.

### 3.2 Instalar
1. Instala **Python 3.12** desde python.org (marca *Add python.exe to PATH*). Si usas `INSTALAR.bat` (sección 3.7), lo instala él si falta.
2. Copia esta carpeta completa al PC. Si traes también `data/` y `models/` desde
   el PC donde se hizo la investigación, te ahorras la descarga y el entrenamiento.
3. En PowerShell, dentro de la carpeta:
   ```bash
   powershell -ExecutionPolicy Bypass -File scripts\install_windows.ps1
   ```
4. Abre el fichero `.env` que se ha creado y pega el token en `DISCORD_BOT_TOKEN`.
   **Con eso ya funciona**; lo demás del `.env` es opcional.

### 3.3 Meter el bot en tu servidor
```bash
.venv\Scripts\python.exe -m finance_bot invitar
```
Te imprime un enlace ya preparado con los permisos justos (leer, escribir,
embeds, fijar el panel). Ábrelo, elige tu servidor y autoriza.

El bot se ata al servidor donde esté al arrancar y publica en el primer canal
donde pueda escribir (prefiere uno que se llame *señales*, *trading* o
*alertas*). Te lo dice en el log. Si quieres fijarlo, activa en Discord
*Ajustes → Avanzado → Modo desarrollador*, copia los IDs con clic derecho y
ponlos en `DISCORD_GUILD_ID` y `DISCORD_CHANNEL_ID`.

En cualquier otro servidor el bot no responde. Si además rellenas
`DISCORD_ADMIN_USER_IDS` con tu ID de usuario, solo tú podrás cambiar ajustes
(`/capital`, `/modo`, `/silenciar`, `/reactivar`). Las señales y los avisos
de cierre mencionan a `@here` (se cambia en `config/settings.yaml`,
`notifications.mention`: `here`, `admins` o `none`).

### 3.4 Datos y modelo (solo si no los has copiado)
```bash
.venv\Scripts\python.exe -m finance_bot download
```
```bash
.venv\Scripts\python.exe -m finance_bot research
```
La descarga es lenta (1–2 h): el servidor gratuito de Dukascopy limita la
velocidad y el descargador lo respeta a propósito. Es reanudable.

### 3.5 Comprobar y arrancar
```bash
.venv\Scripts\python.exe -m finance_bot check
```
Con **MetaTrader 5 abierto** y la sesión de tu cuenta demo iniciada, `check`
debe decir `Fuente en vivo: MT5 OK`. Sin MT5 usa Dukascopy con ~1 h de retraso
(sin M15), y con ese retraso el bot **bloquea** las señales por llegar tarde:
para recibir algo, MT5 tiene que estar abierto.

Arrancar a mano (doble clic en `scripts\run_bot.bat`) o:
```bash
.venv\Scripts\python.exe -m finance_bot run
```

Para que arranque solo al encender el PC y se reinicie si se cae:
```bash
powershell -ExecutionPolicy Bypass -File scripts\install_windows.ps1 -RegisterTask
```

### 3.6 Dónde dejarlo funcionando
El bot tiene que estar encendido para vigilar el mercado, y **necesita
MetaTrader 5, que solo existe para Windows**. Un servidor de Discord no aloja
nada: es una sala de chat. Por eso el sitio natural es un PC con Windows que se
quede encendido; con la tarea programada del paso anterior arranca solo y se
reinicia si falla.

Un VPS Linux barato no sirve igual: sin MT5 tendría que usar Dukascopy con 1 h
de retraso y el bot descartaría las señales por tardías. Si algún día quieres
un servidor 24/7 de verdad, sería un VPS **Windows** con MT5 instalado; el
código es el mismo.

### 3.7 Mover el bot a otro PC (el que se quede encendido)
En el PC donde está ahora:
```bash
powershell -ExecutionPolicy Bypass -File scripts\preparar_traslado.ps1
```
Deja en el Escritorio `FinanceBot-traslado.zip` (~90 MB) con el código, el
histórico ya descargado, el modelo entrenado y tu `.env`. **Ese ZIP lleva tu
token: trátalo como una llave.**

En el PC nuevo:
1. Copia el ZIP (USB o carpeta compartida) y extráelo donde quieras.
2. Doble clic en **INSTALAR.bat**. Instala Python si falta, las dependencias,
   registra el arranque automático, desactiva la suspensión del PC, lanza el bot
   y te dice qué fuente de datos ha encontrado.
3. Instala MetaTrader 5 y entra **una vez** en tu cuenta demo marcando *Guardar
   datos de la cuenta*. A partir de ahí el bot abre el terminal él solo.

Si el bot arranca antes de que MT5 esté listo, no se queda colgado con datos
retrasados: reintenta cada 10 minutos y se pasa a tiempo real en cuanto puede.

El bot arranca **al iniciar sesión** en Windows: deja ese usuario con la sesión
iniciada. Si quieres que entre solo al encender el PC, activa el inicio de
sesión automático (`netplwiz`).

Cuando el bot esté funcionando en el PC nuevo, quítalo del viejo para no tener
dos bots escribiendo a la vez. Lo reversible es **desactivar** la tarea:
```bash
schtasks /change /tn FinanceBot /disable
```

Y si algún día quieres volver a usar este PC, se reactiva igual de fácil:
```bash
schtasks /change /tn FinanceBot /enable
```

Para borrarla del todo:
```bash
schtasks /delete /tn FinanceBot /f
```

> Estos comandos usan `schtasks` a propósito: los equivalentes de PowerShell
> llevan `-Confirm:$false`, y si los envuelves en comillas dobles el `$false` se
> convierte en texto y fallan con un error de tipos.

---

## 4. Uso diario

Todo se maneja con comandos de barra dentro de tu servidor: escribe `/` y
Discord te los ofrece con su descripción.

| Comando | Qué hace |
|---|---|
| `/analisis` | Todo lo que ve el bot ahora: sesgo por temporalidad, votos por familia, niveles, setups y por qué se emiten o no |
| `/senales` | Señales abiertas, con su R actual |
| `/stats` | Resultados reales (7d, 30d, 90d, todo) frente a lo que predijo el modelo |
| `/validacion` | Qué combinaciones tienen ventaja validada y con qué números |
| `/riesgo` | Lotes para un stop dado y tu % de riesgo |
| `/calendario` | Noticias de alto impacto USD/EUR de los próximos días |
| `/capital` · `/riesgo_pct` | Tu capital y tu riesgo por operación |
| `/modo` | Estricto (solo lo validado) o informativo (también setups sin ventaja, marcados con ⚠️) |
| `/seguir` | Vigila una operación tuya (instrumento, compra/venta, entrada, stop) y te avisa al tocar TP1, al cerrarse y cuando convenga salir antes |
| `/dejar` | Deja de vigilar tus operaciones manuales |
| `/silenciar` | Silencia un instrumento unas horas |
| `/estado` | Salud del sistema: datos, fuente, modelo, interruptor de seguridad |
| `/reactivar` | Reactiva el bot tras el interruptor de seguridad (después de revisarlo) |
| `/ayuda` | Cómo funciona y qué significa cada cosa |
| `/panel` | Vuelve a crear el panel fijo del canal |

**El panel.** El bot mantiene un mensaje fijado en el canal que se actualiza
solo cada pocos minutos: precio, sesgo de cada temporalidad en los dos
instrumentos, setups activos y señales abiertas con su R en vivo. Es la foto de
todo de un vistazo, sin escribir nada. Sus botones abren el análisis completo,
las abiertas o los resultados, y solo tú los ves (respuestas privadas).

Además recibes: señales nuevas, avisos de cierre, TP1/TP2/stop/cierre por
tiempo, informe diario (21:30 UTC) y semanal, y avisos de problemas de datos.

### Cómo leer una señal
- **Probabilidad TP1**: verás dos cifras. Una es la estimación del modelo. La otra es lo que ocurrió de verdad en los casos parecidos que el modelo no había visto. Si no coinciden, fíate más de lo ocurrido: en la zona alta el modelo tiende a ser optimista. Ninguna de las dos es una promesa.
- **Escalera**: te deja elegir tu objetivo sabiendo lo que pasó históricamente. Recuerda que un objetivo más cercano acierta más, pero no por eso gana más dinero.
- **Expectativa**: lo que ganaron o perdieron de media esos casos, en R y **después de costes**. Es el número que importa.
- **Duración**: lo típico que tardaron esas operaciones, y el cierre por tiempo del plan.
- **Tamaño**: si el lote mínimo arriesga más que tu límite, el bot lo dice («no recomendable»). Con 250 € pasa a menudo en oro H4/D1.

### Cómo leer un aviso de cierre
Llegan con 🚨 (urgente) o ⏳ y dicen en qué R vas si cierras ahora. Son
observaciones de contexto, no parte de lo validado: el backtest aguanta hasta
stop, objetivo o tiempo. Cada aviso queda guardado con su R y, cuando la
operación cierra, el bot te dice si cerrar ahí habría sido mejor o peor. En
`/stats` verás el acumulado: si estos avisos ayudan o te hacen salir antes de
tiempo. Hasta que haya decenas de casos, eso también es ruido.

### Interruptor de seguridad
Se activa solo si el drawdown real supera 12R o si los aciertos reales quedan
significativamente por debajo de lo que predijo el modelo (p < 0,01). Mientras
esté activo no se emiten señales nuevas. **Nunca se reactiva solo**: `/reactivar`.

---

## 5. Mantenimiento
- Cada noche el bot consolida el histórico con Dukascopy (automático).
- `python -m finance_bot research` reentrena el modelo con los datos nuevos
  (conviene hacerlo cada 6 meses; no se hace solo).
- Qué combinaciones tienen ventaja se sigue decidiendo con el periodo de
  validación fijo (hasta 2021). Los datos nuevos no pueden «aprobar» nada por
  sí solos, y es a propósito.
- Los meses nuevos son el único test limpio que queda. Si algún día quieres
  revalidar con ellos, hazlo una sola vez y con las reglas decididas antes de
  mirar. Repetir la investigación ajustando cosas hasta que salga bonita sería
  sobreajuste.
- Si tu cuenta no es XM Standard, ajusta spread y swap en `config/settings.yaml`.

## 6. Enlaces legales (portal de Discord)
El portal rechaza las URL `.md` de GitHub, asi que los documentos estan en HTML
y publicados con GitHub Pages:

- Terminos: <https://pabliniin.github.io/Finance-Bot/terminos-de-servicio.html>
- Privacidad: <https://pabliniin.github.io/Finance-Bot/politica-de-privacidad.html>

Los sirve la rama `gh-pages` del repositorio. Si cambias los documentos en
`docs/`, copialos a esa rama para que la web se actualice.

## 6b. Actualizaciones automaticas
El bot comprueba GitHub al arrancar y cada hora. Si hay un commit nuevo en
`main`, se lo baja, lo aplica y se reinicia solo (la tarea programada lo vuelve
a levantar). Datos, modelo, base de datos y `.env` no se tocan. Si el codigo
nuevo no arranca tres veces seguidas, vuelve solo al anterior.

Para forzarlo a mano: doble clic en `ACTUALIZAR.bat`.

En un PC con `.git` (el de desarrollo) nunca se auto-actualiza: ahi manda git.

## 7. Limitaciones que debes conocer
- El histórico es de Dukascopy; en vivo, con MT5, los precios son los de tu broker. La diferencia es pequeña pero existe.
- El filtro de noticias solo se aplica en vivo: no hay calendario histórico gratuito, así que el backtest no lo incluye.
- M15 solo tiene histórico desde 2021 y, por tanto, menos casos.
- El feed del calendario (ForexFactory) no es oficial y puede fallar. Si falla, el bot avisa, pero no inventa que no hay noticias.
