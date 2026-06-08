# Internacionalización en CodeIgniter 4 (EN/ES)

Guía práctica para aplicaciones CodeIgniter 4 bilingües que soportan
**inglés (`en`)** y **español (`es`)**. Cubre la configuración de locales del
framework, los ficheros de idioma, los paquetes habituales, el almacenamiento
de contenido traducible en columnas JSON y las reglas que deben seguir los
agentes al añadir o modificar texto. Referencia para roles de i18n /
localización y para cualquier agente que genere o revise cadenas visibles por
el usuario.

## Principio: dos locales, siempre ambos

La política es estricta: solo se soportan `en` y `es`. Toda cadena visible por
el usuario y todo campo de contenido traducible debe existir en **ambos**
locales. No se añade un texto en un idioma sin su pareja en el otro.

## Configuración de locales

CodeIgniter 4 gestiona el idioma activo en `app/Config/App.php` (o vía las
variables de entorno equivalentes). Configuración recomendada para un sitio
bilingüe controlado (sin negociación automática por cabecera del navegador):

```php
public string $defaultLocale = 'en';
public bool   $negotiateLocale = false;
public array  $supportedLocales = ['en', 'es'];
```

- `defaultLocale = 'en'`: locale por defecto cuando no se determina otro.
- `negotiateLocale = false`: el idioma no se negocia con `Accept-Language`; se
  fija explícitamente (por ruta, sesión o preferencia de usuario).
- `supportedLocales = ['en', 'es']`: lista cerrada; el framework rechaza
  cualquier locale fuera de ella.

El locale activo se puede leer y fijar en tiempo de ejecución:

```php
$locale = service('request')->getLocale();   // locale actual
service('request')->setLocale('es');          // forzar locale
```

### Rutas con prefijo de locale

Un patrón habitual para exponer el idioma en la URL (`/en/...`, `/es/...`) es
agrupar las rutas con el placeholder `{locale}`, que CI4 mapea automáticamente
al locale activo de la request:

```php
$routes->group('{locale}', static function ($routes) {
    $routes->get('articles', 'Articles::index');
    $routes->get('articles/(:segment)', 'Articles::show/$2');
});
```

Solo se aceptan segmentos que estén en `supportedLocales`; los demás caen al
`defaultLocale`.

## Ficheros de idioma del framework

Las cadenas de UI viven en ficheros PHP por locale bajo la convención del
framework, p. ej. `app/Language/en/` y `app/Language/es/`. Cada fichero
devuelve un array asociativo:

```php
// app/Language/en/Validation.php
return [
    'required' => 'The {field} field is required.',
];
```

```php
// app/Language/es/Validation.php
return [
    'required' => 'El campo {field} es obligatorio.',
];
```

Ficheros típicos por locale: `Validation.php` (mensajes de validación),
mensajes propios de la aplicación (p. ej. `App.php`, `Errors.php`), y
cualquier dominio funcional adicional. El framework resuelve la traducción por
locale activo y cae al `defaultLocale` si falta una clave.

### Lookup de traducciones con `lang()`

Las cadenas se recuperan con el helper `lang()`, que acepta interpolación de
parámetros:

```php
echo lang('Validation.required', ['field' => 'email']);
// EN: The email field is required.
// ES: El campo email es obligatorio.
```

Nunca se escriben literales visibles por el usuario en controladores, vistas o
servicios: siempre pasan por `lang()` y los ficheros de idioma.

## Paquetes habituales de i18n

- **Ficheros de idioma nativos de CI4**: base del sistema de traducción.
- **`codeigniter4/translations`**: paquete oficial que aporta las traducciones
  de las cadenas internas del framework (validación, errores, etc.) en varios
  idiomas, incluidos `en` y `es`.
- **`daycry/codeigniter-language`**: librería que extiende el sistema i18n de
  CI4 (gestión centralizada de idiomas/traducciones) sobre el mecanismo nativo.

Instala las traducciones del framework cuando quieras mensajes internos ya
localizados sin redefinirlos a mano.

## Contenido traducible en columnas JSON

Las traducciones de _contenido_ (datos de negocio, no cadenas de UI) se
almacenan en columnas JSON con la forma `{"en": "...", "es": "..."}`. Esto
permite un único registro por entidad con todas sus variantes idiomáticas:

```json
{
  "title": { "en": "Welcome", "es": "Bienvenido" },
  "body": { "en": "Hello world", "es": "Hola mundo" }
}
```

Con Doctrine (vía `daycry/doctrine`) estas columnas se mapean como `type: json`
y se manipulan como arrays PHP. Para consultar dentro del JSON en PostgreSQL/
MySQL conviene apoyarse en funciones JSON del motor (extensiones de Doctrine
como `scienta/doctrine-json-functions`).

### Registro de idiomas habilitados

Cuando una entidad de configuración necesita saber qué locales tiene activos,
un patrón limpio es guardar un array JSON `languages` (p. ej.
`["en", "es"]`) en la propia entidad. Mantén ese array sincronizado al
habilitar o deshabilitar un idioma para esa entidad: el código de renderizado
y validación debe iterar exactamente sobre los locales activos.

## Renderizado de campos multi-idioma (UI)

Para editar contenido traducible se usa una UI con pestañas por idioma. Un
patrón reutilizable es una macro de plantilla (Twig vía `daycry/twig`, o
vistas nativas) que, dado un campo y la lista de locales activos, renderiza:

- **Campos de texto/área traducibles**: un control por locale, mostrando solo
  el del idioma activo y ocultando los demás. Un pequeño comportamiento JS de
  "pestañas de idioma" alterna la visibilidad sin recargar.
- **Selects traducibles**: un único `<select>` cuyas `<option>` llevan
  atributos `data-{locale}` con el texto por idioma, y el JS de pestañas
  intercambia la etiqueta visible al cambiar de locale.

La clave es que el formulario envía un único valor estructurado
`{"en": ..., "es": ...}` por campo, coherente con el almacenamiento JSON.

## Reglas para agentes

1. Nunca añadas un campo de contenido sin sus entradas `en` **y** `es`.
2. Nunca hardcodees cadenas visibles por el usuario: usa `lang()` y los
   ficheros de idioma.
3. Al añadir una clave de UI, añádela a **ambos** ficheros de locale en el
   mismo cambio (no dejes una sin su pareja).
4. Mantén el array `languages` de una entidad consistente con los locales
   realmente habilitados para ella.
5. Respeta `supportedLocales = ['en', 'es']`: no introduzcas un tercer idioma
   sin actualizar la configuración y todo el contenido existente.
6. Para mensajes internos del framework, prefiere `codeigniter4/translations`
   antes que re-traducir a mano.
