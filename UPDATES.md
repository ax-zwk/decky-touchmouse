# Actualizaciones automáticas (store personalizado de Decky)

Configuración única en el device:

1. Decky → **Settings → General → Store** → canal **Custom**
2. URL del store:
   ```
   https://ax-zwk.github.io/decky-touchmouse/store
   ```
3. Listo: cada nueva versión publicada aparece en la tienda de Decky con el
   botón **Update** (y aviso de actualización en la lista de plugins).

Alternativa sin store (manual): bajar el ZIP de la
[última release](../../releases/latest) y reinstalar con
**Developer → Install Plugin from ZIP File** (pisa la versión anterior;
los settings se conservan).

## Publicar una corrección (mantenedor)

```bash
# 1. editar código y subir la versión en package.json
# 2. un solo comando: build, zip, commit, release y store actualizado
python3 scripts/publish.py "qué cambió"
```
