# CodeIgniter 4 — Frontend y assets

Guía práctica para la capa de presentación de una aplicación CodeIgniter 4 que
usa Twig (`daycry/twig`) como motor de plantillas, Bootstrap 5 y un pipeline de
assets jQuery/ES6 con DataTables, Select2 y un editor TinyMCE. Cubre la
organización de assets, su versionado, los comportamientos JS de la capa de
administración y las macros Twig reutilizables (formularios traducibles,
DataTables, bloques de contenido, SEO). Referencia para agentes que generan o
revisan la UI y las vistas de un módulo CI4.

## Motor de plantillas: Twig sobre CodeIgniter 4

CodeIgniter 4 trae su propio `View` renderer, pero para vistas ricas con
herencia, macros y partials conviene integrar **Twig 3** vía `daycry/twig`.

- Registra los **paths de plantillas** de cada módulo en su `Config/Registrar.php`
  (en una app HMVC con módulos bajo `app/Modules/`), de modo que Twig resuelva
  `modulo/vista.twig` sin rutas absolutas.
- Las vistas comparten **partials** en un directorio común (p. ej.
  `app/Views/partials/`): macros de formulario, tablas, bloques, cabeceras y SEO.
- Accede a servicios desde el controlador (no desde la plantilla): pasa a Twig
  sólo los datos ya preparados. La plantilla presenta, no consulta.

```php
// Controllers/Article.php
return $this->twig->render('article/list', [
    'rows'   => $rows,
    'locale' => service('request')->getLocale(),
]);
```

## Estructura de assets

Organiza los assets propios separados de las librerías de terceros, de modo que
el versionado y el minificado sólo toquen lo tuyo:

```
public/assets/
  js/
    core/              # JS propio de la app
      form-validation.js
      editor.js         # init/teardown del editor WYSIWYG
      language-tabs.js   # conmuta controles por idioma
      bulk-actions.js    # acciones en lote sobre listas
  css/
  third-party/         # librerías externas (no se editan a mano)
  versions.json        # mapa de versiones para cache-busting
```

- El **JS propio** vive en `js/core/`; cada fichero resuelve una
  responsabilidad concreta (validación, editor, idiomas, acciones en lote).
- Las **librerías de terceros** van en `third-party/` y se actualizan vía el
  gestor de paquetes, no editando el fichero distribuido.

## Versionado de assets (cache-busting)

Para invalidar la caché del navegador cuando un asset cambia, mantén un fichero
`versions.json` con un sello (hash o número de build) por asset y aplícalo como
query string al cargarlo:

```json
{ "js/core/editor.js": "a1b2c3", "css/admin.css": "9f8e7d" }
```

```twig
<script src="/assets/js/core/editor.js?v={{ assetVersion('js/core/editor.js') }}"></script>
```

- **Sube el sello** cada vez que el contenido del asset cambie; si no, los
  clientes seguirán sirviendo la versión antigua desde caché.
- El minificado/concatenado para producción se ejecuta en CI (p. ej. con
  `michalsn/minifier`), que regenera los ficheros y actualiza el mapa de
  versiones. No commitees minificados hechos a mano.

## Comportamientos JS de la capa de administración

Los módulos de la zona de administración comparten un puñado de comportamientos
JS reutilizables. Cada uno es autónomo y se engancha por selector/`data-*`, no
por IDs concretos de una pantalla:

- **Validación de formularios** (`form-validation.js`): envío AJAX con
  **prevención de doble submit** (deshabilita el botón hasta recibir respuesta)
  y pintado de errores de validación devueltos por el servidor.
- **Editor WYSIWYG** (`editor.js`): ciclo de vida `init`/`teardown` del editor
  enriquecido (p. ej. TinyMCE). Cuando el editor se abre dentro de un **modal**
  de Bootstrap, aplica el **fix de `z-index`** para que sus diálogos
  (selector de enlaces, tablas) rendericen por encima del modal y no queden
  ocultos.
- **Pestañas de idioma** (`language-tabs.js`): conmuta la visibilidad de los
  controles por locale en formularios traducibles (trabaja con la macro de
  campo descrita abajo).
- **Acciones en lote** (`bulk-actions.js`): selección por checkbox sobre listas
  DataTables + acciones masivas (cambiar visibilidad, borrado lógico) sobre la
  selección.

Reglas para el JS propio:

- ES6, sin estado global oculto; expón cada comportamiento como un init
  idempotente que pueda re-ejecutarse tras una recarga AJAX parcial.
- Re-inicializa los plugins (Select2, DataTables, editor) cuando inyectes HTML
  por AJAX: lo que se monta en `DOMContentLoaded` no cubre el contenido añadido
  después.

## Librerías de UI

Stack de UI recomendado y fijado por versión para evitar drift:

- **Bootstrap 5** (pinéa la versión menor) como sistema de layout y componentes.
- **jQuery 3.x** como base de los plugins clásicos (Select2, DataTables).
- **Select2** para selects con búsqueda/etiquetado.
- **DataTables server-side** vía `hermawan/codeigniter4-datatables`: la tabla
  pide los datos por AJAX al endpoint del módulo, con orden/filtro/paginación
  resueltos en servidor (no carga todas las filas en cliente).

Fija las versiones en el gestor de dependencias y súbelas de forma deliberada;
no dejes rangos abiertos que rompan la UI en un `composer update` rutinario.

## Macro central de campo de formulario

Centraliza el renderizado de campos en una macro Twig única (p. ej.
`partials/input-forms/_field.twig`) en lugar de repetir markup de Bootstrap en
cada vista. Así un cambio de estilo o de comportamiento se aplica en un solo
sitio.

- **Modos**: `standard` (campo dentro de una row de Bootstrap con label) y
  `bare` (sólo el control, para incrustarlo en layouts a medida).
- **Campos traducibles tipo select**: un único `<select>` cuyas `<option>`
  llevan atributos `data-{locale}` con el texto por idioma; el JS de pestañas de
  idioma muestra la etiqueta del locale activo.
- **Campos traducibles que no son select**: se renderiza **un control por
  idioma** y la visibilidad por locale la gobierna `language-tabs.js` (sólo el
  idioma activo es visible).

```twig
{% macro field(name, value, opts = {}) %}
  {% set mode = opts.mode|default('standard') %}
  <div class="{{ mode == 'standard' ? 'row mb-3' : '' }}">
    {% if mode == 'standard' and opts.label is defined %}
      <label class="col-form-label" for="{{ name }}">{{ opts.label }}</label>
    {% endif %}
    <input type="{{ opts.type|default('text') }}"
           id="{{ name }}" name="{{ name }}"
           class="form-control" value="{{ value }}">
  </div>
{% endmacro %}
```

## Otras macros / partials reutilizables

Mantén un catálogo pequeño de partials compartidos y reúsalos en todos los
módulos en lugar de duplicar HTML:

- **`datatable.twig`**: pinta la tabla server-side (cabeceras, `data-*` de
  configuración, columna de acciones); el JS la inicializa contra el endpoint.
- **`blocks.twig`**: sistema de **bloques de contenido** reutilizables entre
  módulos (secciones repetibles que se renderizan por AJAX, con "repeater"
  partials para grupos de campos dinámicos).
- **`language-tabs.twig`**: pestañas de idioma para formularios traducibles.
- **`seo.twig`**: campos de SEO (title, description, slug) compartidos por las
  entidades que los necesitan.
- **`form-section.twig`**: agrupador visual de campos en secciones colapsables.

Convención: la lógica vive en la macro; la vista del módulo sólo la invoca con
sus datos. Si una vista necesita markup nuevo recurrente, extrae una macro
antes de copiar/pegar.

## Listas y CRUD desde la vista

El patrón de lista de un módulo combina la macro de DataTable con las acciones
en lote y un endpoint server-side que el frontend consume:

1. La vista pinta `datatable.twig` apuntando al endpoint de datos del módulo.
2. El backend expone rutas de soporte coherentes: `list/order` (datos y orden),
   `visibility` (alternar el flag `visible`), `delete` (borrado lógico vía
   `deleted_at`). Mantén estos nombres estables para que el JS genérico de
   acciones en lote funcione en todos los listados.
3. Las acciones masivas operan sobre los IDs seleccionados por checkbox y
   refrescan la tabla por AJAX al terminar.

```twig
{% include 'partials/datatable.twig' with {
  endpoint: url_to('article.list'),
  columns:  ['title', 'visible', 'position'],
  bulk:     ['toggle-visibility', 'soft-delete']
} %}
```

## Bloques de contenido y partials por AJAX

Para contenido modular (secciones reordenables, repetidores de campos):

- Cada bloque se renderiza por su propio **partial** que el servidor devuelve
  por AJAX (`partialBlock`, `getBlock`, `validateBlock`, etc., según el contrato
  del controlador del módulo).
- Los **repeaters** clonan un partial-plantilla por cada elemento; el JS
  reindexa los `name="campo[i]"` al añadir/quitar para que el binding de
  servidor sea correcto.
- Tras inyectar un bloque, **re-inicializa** sus plugins (editor, Select2) — no
  asumas que el init global los cubre.

## Buenas prácticas de la capa frontend

- **Re-inicialización idempotente**: todo init JS debe poder ejecutarse de nuevo
  sobre HTML inyectado por AJAX sin duplicar listeners.
- **No mezcles presentación y consulta**: la plantilla recibe datos listos; las
  consultas y la lógica viven en controlador/servicio.
- **Accesibilidad**: usa los componentes accesibles de Bootstrap, labels
  asociados a sus inputs y foco visible; no rompas la navegación por teclado.
- **i18n en la UI**: usa el sistema de idioma de CI4 (`lang('Archivo.clave')`)
  para los textos de la interfaz; el contenido multi-idioma de las entidades va
  en columnas JSON `{"en": "...", "es": "..."}`, no en filas separadas.
- **Versiona los assets** al cambiarlos y deja el minificado a CI; nunca subas
  secretos ni endpoints internos al JS que viaja al navegador.
