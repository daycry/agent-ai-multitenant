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

  # ---- Filesystem: read the image rootfs, confine WRITES to the expected
  #      runtime dirs. The host rootfs stays read-only. ----
  /                         r,
  /**                       r,
  /usr/**                    rix,
  /bin/**                   rix,
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
