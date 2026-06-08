# Modelo de datos en CodeIgniter 4 con Doctrine ORM

Guía práctica de la capa de persistencia para aplicaciones CodeIgniter 4 que
integran Doctrine ORM 3.x vía `daycry/doctrine ^5`, con mapeo por atributos PHP
(`#[ORM\...]`), UUID de Ramsey, funciones JSON de Scienta y Second-Level Cache
sobre Redis. Referencia para agentes que diseñan entidades, repositorios,
migraciones y caché en este stack.

## Stack de persistencia

- **Doctrine ORM 3.x** integrado en CodeIgniter 4 con el paquete
  `daycry/doctrine` (configura el `EntityManager`, drivers de mapeo y CLI
  `php spark`).
- **Mapeo por atributos PHP** (`#[ORM\...]`), no XML ni YAML.
- **`ramsey/uuid-doctrine`** para columnas UUID.
- **`scienta/doctrine-json-functions`** para consultar columnas JSON desde DQL
  (`JSON_EXTRACT`, `JSON_SET`, ...).
- Base de datos relacional MySQL 8+ / MariaDB 10.5+ (driver MySQLi de CI4) con
  charset `utf8mb4`. Doctrine también soporta PostgreSQL si el proyecto lo usa.

## Patrón BaseEntity (MappedSuperclass)

Define una `BaseEntity` como `#[ORM\MappedSuperclass]` con
`#[ORM\HasLifecycleCallbacks]` de la que heredan todas las entidades, para
centralizar PK, UUID, timestamps y soft-delete:

```php
#[ORM\MappedSuperclass]
#[ORM\HasLifecycleCallbacks]
abstract class BaseEntity
{
    #[ORM\Id]
    #[ORM\Column(type: 'integer')]
    #[ORM\GeneratedValue(strategy: 'IDENTITY')]
    protected ?int $id = null;

    #[ORM\Column(type: 'string', length: 50, unique: true)]
    protected string $uuid = '';

    #[ORM\Column(type: 'datetime')]
    #[ORM\Index(name: 'idx_created_at')]
    protected ?\DateTimeInterface $created_at = null;

    #[ORM\Column(type: 'datetime', nullable: true)]
    #[ORM\Index(name: 'idx_updated_at')]
    protected ?\DateTimeInterface $updated_at = null;

    #[ORM\Column(type: 'datetime', nullable: true)]
    #[ORM\Index(name: 'idx_deleted_at')]
    protected ?\DateTimeInterface $deleted_at = null;
}
```

- `id`: PK entera auto-incremental (`strategy: 'IDENTITY'`).
- `uuid`: `varchar(50)` único, generado en `prePersist` para uso distribuido y
  para exponerlo en la API sin filtrar la PK interna.
- `created_at` / `updated_at` / `deleted_at`: cada uno con índice nombrado;
  `deleted_at` habilita **soft-delete** (nunca se borra físicamente el
  contenido).

## Lifecycle callbacks

Los callbacks de ciclo de vida rellenan UUID y timestamps automáticamente:

```php
#[ORM\PrePersist]
public function prePersist(): void
{
    if ($this->uuid === '') {
        $this->uuid = Uuid::uuid4()->toString();
    }
    $this->created_at = new \DateTime();
}

#[ORM\PreUpdate]
public function preUpdate(): void
{
    $this->updated_at = new \DateTime();
}
```

## Serialización selectiva

Para no exponer la entidad cruda en la API, marca qué propiedades se serializan
con grupos de exclusión (por ejemplo JMS Serializer): política `all` por defecto
y `expose` explícito por grupo (`timestamps`, `blocks`, ...). De este modo cada
endpoint elige el grupo a emitir y evita fugar campos internos.

## Soft-delete y borrado lógico

- El borrado es **lógico**: se fija `deleted_at` en vez de ejecutar `DELETE`.
- A nivel de BD usa `ON DELETE CASCADE` en las FK donde la semántica lo exija; a
  nivel Doctrine declara `cascade: ['persist', 'remove']` en las asociaciones
  que deban propagarse.
- Todo borrado lógico debe **invalidar las claves de Second-Level Cache**
  afectadas para no servir contenido obsoleto.
- Las consultas de lectura deben filtrar `deleted_at IS NULL` (centralízalo en
  el repositorio o con un Doctrine filter, no lo dejes a cada consulta suelta).

## Patrón Config + Items

Para módulos con una configuración singleton más una colección de elementos,
modela un par **Config + Items**:

- Una entidad `*_config` (1:1 con su contenedor) que agrega ajustes
  transversales (textos traducibles, logos, redes sociales, categorías, CSP,
  cookies, ...), normalmente en columnas JSON.
- N entidades de ítem, cada una con un flag `visible` (booleano) y una columna
  `position` (entero) para ordenar, además de sus descripciones y contenido.

Ejemplo neutro: un módulo "noticias" tendría `news_config` (singleton) más
`news_item` (N elementos con `visible`, `position`, `related_items` JSON, ...).

## Columnas JSON y multi-idioma

Las columnas JSON son habituales para contenido multi-idioma y configuración
semiestructurada. Para texto bilingüe, usa el formato `{"es": "...", "en": "..."}`
(coherente con la política EN/ES de la plataforma):

```php
#[ORM\Column(type: 'json')]
private array $title = ['es' => '', 'en' => ''];
```

- Consulta el contenido JSON desde DQL con las **funciones JSON de Scienta**
  (`JSON_EXTRACT(e.title, '$.es')`, `JSON_SET`, ...), registrándolas en la
  configuración de DQL de Doctrine.
- No metas en JSON lo que es claramente relacional y se consulta/junta a menudo:
  perderías integridad referencial y planificación de consultas. Reserva JSON
  para traducciones, ajustes y estructuras flexibles (SEO, banners, galerías,
  redes sociales).

## Repositorios y consultas

- Las consultas personalizadas viven en repositorios por módulo
  (`Models/Repositories/*.php`), nunca en el controlador.
- Usa QueryBuilder o DQL; reserva SQL crudo para casos puntuales de rendimiento.
- Cuidado con el problema **N+1**: usa `JOIN` + `addSelect` o fetch eager
  dirigido cuando vayas a recorrer asociaciones.
- `flush()` una vez por unidad de trabajo, no dentro de bucles.
- Para resolución de colecciones ordenadas, el idioma de Doctrine `Criteria` +
  un `usort` posterior permite honrar un orden solicitado por el cliente con
  fallback manual cuando la colección no soporta `matching()`.

## Identificadores

- PK entera interna (`IDENTITY`) por compacidad y localidad de índice.
- `uuid` adicional como identificador opaco para la API y sistemas
  distribuidos; evita enumeración y colisiones entre orígenes.
- No reutilices claves naturales mutables como PK.

## Migraciones y seeds

- Migraciones **por módulo**, bajo la estructura del módulo (p. ej.
  `Modules/{Zona}/{Modulo}/Database/Migrations/`).
- Cada migración debe ser **reversible**: implementa `down()` de verdad y
  pruébalo. No edites una migración ya aplicada en otros entornos; crea una
  nueva (patrón expand/contract para cambios online-safe).
- No pongas datos sensibles ni lógica de negocio en migraciones.
- Los seeds viven en `Database/Seeds/` del propio módulo.
- Excluye `Migrations/`, `Seeds/` y `Proxies/` de phpcpd, PHPStan y cobertura.

## Second-Level Cache (SLC)

Doctrine SLC reduce la carga de lectura cacheando entidades y colecciones:

- **Regiones nombradas** por perfil de acceso, por ejemplo `entity_read_heavy`,
  `entity_mixed`, `collection_*`.
- Backend de caché **PSR-6 sobre Redis**; combina caché de entidad, de consulta
  y de metadatos.
- Servicios de dominio que dependen de lecturas calientes (por ejemplo un
  servicio de menú) pueden apoyarse en SLC.
- Los repositorios deben **invalidar las claves concretas** en `persist`,
  `update` y `remove` (incluido el soft-delete) para no servir datos obsoletos.

## Generación de proxies

Doctrine genera clases proxy para lazy-loading de asociaciones:

```bash
php spark DoctrineProxies   # regenera Models/Proxies/
```

- En desarrollo/test pueden regenerarse al vuelo (`autoGenerateProxyClasses`).
- En producción, **envía los proxies pre-generados** en el artefacto de
  despliegue (autogeneración desactivada).
- Excluye `Proxies/` de cs-fixer, PHPStan y cobertura.

## Tablas de autenticación (daycry/auth)

Si el proyecto usa `daycry/auth`, su esquema relacional incluye típicamente
`users` (con soft-delete), identidades, grupos, permisos, tablas de unión
grupo-usuario y permiso-usuario, intentos de login, tokens "recordarme" y logs.
Registra los intentos de login **sólo en fallo** (production-safe) y define los
grupos por defecto según el modelo de roles de la aplicación. El detalle de
configuración de autenticación pertenece a la KB de seguridad.
