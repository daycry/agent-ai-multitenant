# AppArmor profile for the platform's long-lived services (Plan 15 task_15_16).
#
# Layered defence on top of the default-deny seccomp profile (task_15_15),
# cap_drop ALL + no-new-privileges (task_06_14_11) and the read-only mounts: it
# is a Mandatory Access Control confinement that the host kernel enforces. The
# profile DENIES the dangerous primitives a container escape relies on
# (raw mount/umount/pivot_root, ptrace of other tasks, loading kernel modules,
# rebooting, raw I/O) and CONFINES writes to the dirs a service actually needs
# (its data volume under /var/lib, /data, /tmp, /run), keeping the host rootfs
# and host-sensitive paths (/proc/sys writes, /sys writes, /boot, the docker
# socket) read-only or denied.
#
# It is DELIBERATELY less strict than agent-runtime.profile: these services run
# the platform's own (trusted) code, so they keep file/network access to do
# their job; the untrusted agent/test sandbox uses the stricter profile.
#
# LOADING (host / HUMAN step — cannot run in CI, no privileged kernel):
#   sudo apparmor_parser -r -W docker/apparmor/agentic-default.profile
#   docker compose ... up -d   # security_opt: apparmor=agentic-default pins it
# Verify it is loaded:  sudo aa-status | grep agentic-default
# See docs/06-runbooks/internal-pentest-methodology.md §5 + the loading note
# in docs/06-runbooks/apparmor-profiles.md.

#include <tunables/global>

profile agentic-default flags=(attach_disconnected,mediate_deleted) {
  #include <abstractions/base>

  # ---- Network: the services talk over the compose bridge networks. ----
  network inet  tcp,
  network inet  udp,
  network inet6 tcp,
  network inet6 udp,
  network unix  stream,
  network unix  dgram,
  network netlink raw,

  # ---- Capabilities: nothing beyond what cap_drop ALL already removes.
  #      We do NOT grant sys_admin / sys_module / sys_ptrace / sys_boot. ----
  capability chown,
  capability dac_override,
  capability fowner,
  capability fsetid,
  capability setgid,
  capability setuid,
  capability net_bind_service,

  # Las dos que faltaban, y por qué no son un ensanchamiento (2026-08-28).
  #
  # El compose generado concede `cap_add: [IPC_LOCK, SETFCAP, …]` a Vault, y el
  # stack vivo confirma que las tiene. Pero este perfil no las listaba, así que
  # AppArmor las denegaba DESPUÉS de que Docker las concediera:
  #
  #   vault-1 | unable to set CAP_SETFCAP effective capability: Operation not permitted
  #
  # …y Vault en bucle, y la instalación abortando en `start_stack` (e2e run
  # 33174222896). Vault las necesita por diseño: `SETFCAP` porque su entrypoint
  # hace `setcap` sobre su binario, e `IPC_LOCK` para `mlock`, que es lo que
  # impide que las claves acaben en swap — desactivarlo sería el arreglo malo.
  #
  # Listarlas aquí NO se las da a nadie más. Una regla `capability` de AppArmor
  # es un TECHO, no una concesión: un servicio con `cap_drop: ALL` y sin
  # `cap_add` sigue sin poder usarlas porque Docker se las quitó antes. Lo único
  # que cambia es que AppArmor deje de ser la segunda denegación para quien sí
  # las tiene legítimamente.
  #
  # `tests/unit/test_apparmor_profile_stays_narrow.py` cruza esta lista con las
  # que el generador concede: si mañana un servicio pide una capacidad nueva y
  # nadie la añade aquí, la suite lo dice — en vez de descubrirlo en una
  # instalación real, que es como se descubrió ésta.
  capability ipc_lock,
  capability setfcap,

  # ---- Filesystem: read the image rootfs, confine WRITES to the expected
  #      runtime dirs. The host rootfs stays read-only. ----
  /                         r,
  /**                       r,
  /usr/**                    rix,
  /bin/**                   rix,

  # EXCEPCIÓN, estrecha y con motivo (2026-08-28).
  #
  # El `docker-socket-proxy` es HAProxy, y su entrypoint GENERA su propia
  # configuración al arrancar a partir de las variables de la ACL (CONTAINERS=1,
  # EXEC=0…). Escribe en `/usr/local/etc/haproxy/haproxy.cfg`, que cae bajo el
  # `/usr/** rix` de arriba — sin `w`. Resultado medido en la primera ejecución
  # real de este perfil (e2e run 33171640034):
  #
  #   docker-entrypoint.sh: line 18: can't create
  #     /usr/local/etc/haproxy/haproxy.cfg: Permission denied
  #
  # …y con él, el contenedor en bucle, su healthcheck en `Error`, y la
  # instalación entera abortando en `start_stack`. No es un servicio accesorio:
  # es el que sostiene el socket de Docker por el Principio 2.
  #
  # Se abre SÓLO ese directorio, no `/usr/**`. Lo que hay dentro es config
  # generada en cada arranque, no binarios: escribir ahí no permite sustituir un
  # ejecutable que otro proceso vaya a correr, que es de lo que protege la regla
  # general. `tests/unit/test_apparmor_profile_stays_narrow.py` comprueba que la
  # excepción no se ensancha.
  /usr/local/etc/haproxy/    rw,
  /usr/local/etc/haproxy/**  rwk,

  /sbin/**                  rix,
  /lib/**                   rix,
  /lib64/**                 rix,
  /etc/**                   r,

  # Writable runtime dirs only (data volumes, scratch, runtime state).
  /tmp/                     rw,
  /tmp/**                   rwk,
  /run/                     rw,
  /run/**                   rwk,
  /var/run/**               rwk,
  /var/lib/**               rwk,
  /var/log/**               rwk,
  /data/                    rw,
  /data/**                  rwk,
  /vault/**                 rwk,
  /prometheus/**            rwk,
  /alertmanager/**          rwk,
  # Caddy autosaves its JSON config under XDG_CONFIG_HOME=/config (ADR 0061).
  /config/                  rw,
  /config/**                rwk,
  /proc/*/fd/**             rw,
  owner /proc/*/**          rw,

  # ---- DENY the container-escape / host-tamper primitives outright. ----
  deny capability sys_admin,
  deny capability sys_module,
  deny capability sys_ptrace,
  deny capability sys_boot,
  deny capability sys_rawio,
  deny capability mac_admin,
  deny capability mac_override,

  # Raw mount / namespace pivot — the classic escape primitive.
  deny mount,
  deny umount,
  deny remount,
  deny pivot_root,

  # ptrace of other tasks (read another container's memory / inject).
  deny ptrace (read, trace),

  # Host-sensitive paths: deny writes to kernel knobs and boot/module state,
  # and deny ANY access to the docker socket (a socket leak == host takeover).
  deny /proc/sys/** wklx,
  deny /proc/sysrq-trigger wklx,
  deny /proc/kcore rwklx,
  deny /sys/** wklx,
  deny /boot/** rwklx,
  deny /lib/modules/** wklx,
  deny /dev/mem rwklx,
  deny /dev/kmem rwklx,
  deny /dev/port rwklx,
  deny /var/run/docker.sock rwklx,
  deny /run/docker.sock rwklx,
}
