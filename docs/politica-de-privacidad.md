# Política de Privacidad — Finance Bot

*Última actualización: 22 de septiembre de 2026*

Finance Bot es un bot de Discord privado y de uso personal. Esta política
explica exactamente qué datos trata, para qué y dónde se guardan.

## 1. Qué datos trata

| Dato | Para qué | Dónde se guarda |
|---|---|---|
| ID del servidor y del canal de Discord | Saber dónde puede responder y publicar | Fichero de configuración y base de datos local |
| ID de usuario de Discord de los administradores | Comprobar quién puede cambiar ajustes y a quién mencionar en una señal | Fichero de configuración local |
| Preferencias del bot (capital de referencia, % de riesgo, modo, silencios) | Adaptar los cálculos y los avisos | Base de datos local (SQLite) |
| Señales generadas por el propio bot y su seguimiento | Calcular resultados y estadísticas | Base de datos local (SQLite) |

Todo ello se guarda **en el ordenador del propietario del bot**, en la carpeta
del proyecto. No hay servidores externos, ni copias en la nube, ni base de datos
compartida.

## 2. Qué datos NO trata

- **No lee los mensajes del servidor.** El bot funciona con comandos de barra y
  no tiene activado el permiso de contenido de mensajes (*Message Content
  Intent*), así que técnicamente no puede leer lo que escribís.
- **No guarda información personal**: ni nombres, ni correos, ni datos de
  contacto, ni datos de tu bróker, ni credenciales de trading.
- **No accede a cuentas de trading.** Lee precios de MetaTrader 5 en modo
  lectura; no puede operar ni consultar tu saldo real.
- No usa cookies ni rastreadores: no hay web.

## 3. Con quién se comparte

Con nadie. Los datos no se venden, no se ceden a terceros, no se usan para
publicidad ni para entrenar servicios externos.

El bot sí se **conecta** a estos servicios de terceros para funcionar, enviando
solo lo imprescindible (peticiones de datos de mercado, sin información sobre
ti):

- **Discord** (API oficial) — para recibir comandos y publicar mensajes.
- **Dukascopy** — histórico y precios de XAUUSD y EURUSD.
- **ForexFactory** — calendario económico de noticias de alto impacto.
- **MetaTrader 5** — precios en vivo, en local, desde el terminal del propietario.

## 4. Cuánto tiempo se conservan

Las preferencias y el historial de señales se conservan mientras el bot esté en
uso, porque son necesarios para calcular sus estadísticas. Se pueden borrar en
cualquier momento eliminando el fichero de base de datos del bot.

## 5. Tus derechos

Como los datos tratados son IDs públicos de Discord y ajustes del propio bot,
basta con pedirlo al propietario para que se borren, o expulsar al bot del
servidor: a partir de ahí deja de tratar dato alguno de ese servidor.

## 6. Seguridad

El token del bot y cualquier credencial viven en un fichero local `.env` que
está excluido del repositorio público. El código fuente completo se puede
auditar en <https://github.com/Pabliniin/Finance-Bot>.

## 7. Contacto

Responsable: **Pablo** (autor del repositorio
<https://github.com/Pabliniin/Finance-Bot>).
Para ejercer cualquier derecho o preguntar por esta política, abre una incidencia
en ese repositorio o escribe por mensaje directo de Discord al propietario del
servidor donde esté el bot.
