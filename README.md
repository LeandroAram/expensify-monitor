# expensify-monitor

Monitor de trabajos pagos de [Expensify/App](https://github.com/Expensify/App) que corre en GitHub Actions (repo público: sin cargo).

## Qué detecta

Una issue de Expensify/App **califica** cuando está abierta, tiene las etiquetas `External` **y** `Help Wanted`, y el título tiene precio (`[$250]`).

- **nueva**: una issue pasa a calificar (aunque se haya creado días antes: se revisan todas las issues actualizadas desde la última ejecución, no solo las creadas).
- **reapertura**: una issue que califica fue reabierta (estaba cerrada, o se cerró y reabrió entre dos ejecuciones).

La primera ejecución toma el estado actual como **línea base** y no avisa de las existentes.

## Cómo avisa

Por cada detección crea una issue en este repo con etiqueta `oportunidad`, mención a @LeandroAram, enlace a la issue de Expensify y una línea para reanudar la sesión de Devin.

## Funcionamiento

- `.github/workflows/monitor.yml`: cada ejecución revisa cada 10 minutos durante unas 5,5 horas y al terminar se relanza sola con `workflow_dispatch`, porque el cron de GitHub puede pasar horas sin correr; el cron queda como respaldo. Usa solo `GITHUB_TOKEN` y es gratis en repos públicos.
- Estado (cursor + issues seguidas) en la rama `estado`.
- **Apagado automático**: desde el 30/11/2026 23:59:59 (hora argentina) el script deshabilita el workflow y no vuelve a consultar.

## Prueba manual

Actions → *Monitor Expensify* → *Run workflow* → `simular_issue` = número de una issue que califica. Crea un aviso marcado `[PRUEBA]` sin tocar el estado.

## Local

```bash
python -m unittest discover -s tests -t .
GITHUB_TOKEN=... NOTIFY_REPO=LeandroAram/expensify-monitor python monitor.py --state /tmp/state.json --dry-run
```
