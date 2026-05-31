# Convenciones de stack: PHP + Symfony

Guía práctica para aplicaciones Symfony 6/7 con PHP 8.2+, Doctrine ORM, tests
con PHPUnit y migraciones con doctrine-migrations. Referencia para agentes que
generan o revisan código Symfony.

## Estructura del proyecto

Symfony impone una estructura; respétala:

```
src/
  Controller/   # controladores finos (acción -> service)
  Entity/       # entidades Doctrine
  Repository/   # repositorios Doctrine (consultas)
  Service/      # lógica de negocio
  Dto/          # objetos de transferencia (request/response)
  EventListener/
config/         # services.yaml, packages/, routes/
migrations/     # migraciones Doctrine versionadas
tests/
  Unit/
  Functional/
```

Regla: los controladores no contienen lógica de negocio; delegan en services.
Las entidades no contienen lógica de aplicación, sólo estado e invariantes.

## PHP moderno

- `declare(strict_types=1);` en todos los ficheros.
- Tipos en todas las firmas: parámetros, retornos, propiedades.
- Constructor property promotion y `readonly` para inmutabilidad.
- Enums nativos en lugar de constantes sueltas.

```php
final class CreateProject
{
    public function __construct(
        public readonly string $name,
        public readonly ?string $description = null,
    ) {}
}
```

## Inyección de dependencias y autowiring

Symfony cablea servicios por type-hint del constructor. No instancies servicios
con `new`; pídelos por inyección.

```php
final class ProjectService
{
    public function __construct(
        private readonly ProjectRepository $projects,
        private readonly EntityManagerInterface $em,
    ) {}
}
```

- Marca los services como `final` salvo que necesites extenderlos.
- Evita el container como service locator; inyecta lo que usas.
- Usa interfaces para puntos de extensión; bindea la implementación en
  `services.yaml`.

## Controladores y rutas

Usa atributos de PHP 8 para routing:

```php
#[Route('/projects', name: 'project_')]
final class ProjectController extends AbstractController
{
    #[Route('/{id}', name: 'show', methods: ['GET'])]
    public function show(string $id, ProjectService $service): JsonResponse
    {
        $project = $service->get($id);
        if ($project === null) {
            throw $this->createNotFoundException('project not found');
        }
        return $this->json($project);
    }
}
```

- Devuelve `JsonResponse` desde DTOs/arrays, nunca expongas la entidad cruda.
- Valida el input con el componente Validator + DTOs, no en el controlador.

## Doctrine ORM

- Mapea entidades con atributos:

```php
#[ORM\Entity(repositoryClass: ProjectRepository::class)]
#[ORM\Table(name: 'projects')]
class Project
{
    #[ORM\Id]
    #[ORM\Column(type: 'uuid')]
    private Uuid $id;

    #[ORM\Column]
    private string $tenantId;
}
```

- Consultas en el Repository, no en el controlador. Usa el QueryBuilder o DQL;
  evita SQL crudo salvo necesidad de rendimiento.
- Cuidado con N+1: usa `JOIN` + `addSelect` o fetch eager dirigido.
- `flush()` una vez por unidad de trabajo, no en bucle.
- No abuses del `EntityManager` en services de dominio puro; aísla la
  persistencia en repositorios.

### Multi-tenancy

Toda consulta filtra por `tenantId`. Centraliza el filtro en el repositorio o
con un Doctrine filter activado por el tenant del request; no dejes que un
controlador olvide acotarlo.

## Validación

Usa el componente Validator con atributos en los DTOs:

```php
final class CreateProjectInput
{
    #[Assert\NotBlank]
    #[Assert\Length(max: 120)]
    public string $name = '';
}
```

Valida en el borde y deja que el dominio asuma datos correctos.

## Migraciones con doctrine-migrations

- Genera migraciones con `doctrine:migrations:diff`, revísalas a mano antes de
  commitear (el diff no siempre acierta).
- Cada migración debe ser **reversible**: implementa `down()` de verdad.
- No edites una migración ya aplicada en otros entornos; crea una nueva.
- Nunca pongas datos sensibles ni lógica de negocio en migraciones.

## Manejo de errores

- Lanza excepciones de dominio propias; un `ExceptionListener` las mapea a
  respuestas HTTP coherentes.
- No expongas trazas en producción (`APP_ENV=prod`, `APP_DEBUG=0`).
- Loguea con Monolog incluyendo un id de correlación.

## Testing con PHPUnit

- **Unit**: services con dependencias dobladas (mocks/stubs), sin kernel.
- **Functional**: extiende `WebTestCase`, lanza requests contra el kernel:

```php
final class ProjectControllerTest extends WebTestCase
{
    public function testShowNotFound(): void
    {
        $client = static::createClient();
        $client->request('GET', '/projects/unknown');
        self::assertResponseStatusCodeSame(404);
    }
}
```

- DB de test aislada; usa transacciones que se revierten o una BD efímera.
- Apunta a >70% de cobertura en la capa de dominio/servicios.

## Tooling

- PHP-CS-Fixer (PSR-12) + PHPStan (nivel alto) + Psalm opcional.
- `composer.lock` commiteado.
- Pre-commit / CI que ejecute cs-fixer --dry-run, phpstan y phpunit.
