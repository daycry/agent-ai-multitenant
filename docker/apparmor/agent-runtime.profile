# AppArmor profile for the UNTRUSTED agent / test runtime sandbox
# (Plan 15 task_15_16). This is the strictest profile in the platform: it
# confines the container that runs HOSTILE agent / test code (ADR 0012,
# CLAUDE.md §2). It pairs with the stricter seccomp profile
# docker/seccomp/agent-runtime.json, cap_drop ALL, no-new-privileges, a
# read-only rootfs and an internal-only network (workers.isolation).
#
# Posture: like agentic-default it DENIES the escape primitives (mount,
# pivot_root, ptrace, kernel modules, reboot, raw I/O, docker.sock), but it is
# STRICTER about WRITES — the agent may only write under /workspace and /tmp
# (the two tmpfs / bind mounts the worker hands it); EVERYTHING ELSE on the
# filesystem is read-only or denied. There is no broad /var/lib or /data write
# grant here: untrusted code gets the minimum surface to do its job.
#
# LOADING (host / HUMAN step — cannot run in CI, no privileged kernel):
#   sudo apparmor_parser -r -W docker/apparmor/agent-runtime.profile
#   # the worker pins it via WORKERS_APPARMOR_PROFILE=agent-runtime
#   # -> security_opt: apparmor=agent-runtime (apps/workers .../isolation.py)
# Verify:  sudo aa-status | grep agent-runtime
# See docs/06-runbooks/apparmor-profiles.md + internal-pentest-methodology.md §5.

#include <tunables/global>

profile agent-runtime flags=(attach_disconnected,mediate_deleted) {
  #include <abstractions/base>

  # ---- Network: ONLY the internal agents bridge → egress-proxy (ADR 0019).
  #      No raw netlink (no route/interface tampering). ----
  network inet  tcp,
  network inet6 tcp,
  network unix  stream,

  # ---- Capabilities: none beyond what is needed to drop to the agent uid.
  #      Deliberately NO net_bind_service, NO dac_override. ----
  capability setgid,
  capability setuid,

  # ---- Filesystem: read the image rootfs to run the toolchain; WRITES are
  #      confined to /workspace and /tmp ONLY. ----
  /                         r,
  /**                       r,
  /usr/**                   rix,
  /bin/**                   rix,
  /sbin/**                  rix,
  /lib/**                   rix,
  /lib64/**                 rix,
  /etc/**                   r,

  # The ONLY writable locations the untrusted code gets.
  /workspace/               rw,
  /workspace/**             rwkix,
  /tmp/                     rw,
  /tmp/**                   rwk,
  owner /proc/*/fd/**       rw,
  owner /proc/*/task/**     r,

  # ---- DENY the container-escape / host-tamper primitives outright. ----
  deny capability sys_admin,
  deny capability sys_module,
  deny capability sys_ptrace,
  deny capability sys_boot,
  deny capability sys_rawio,
  deny capability sys_chroot,
  deny capability net_admin,
  deny capability net_raw,
  deny capability mac_admin,
  deny capability mac_override,
  deny capability dac_override,
  deny capability dac_read_search,

  deny mount,
  deny umount,
  deny remount,
  deny pivot_root,
  deny ptrace (read, trace),

  # Host-sensitive paths fully denied for untrusted code.
  deny /proc/sys/** wklx,
  deny /proc/sysrq-trigger wklx,
  deny /proc/kcore rwklx,
  deny /sys/** rwklx,
  deny /boot/** rwklx,
  deny /lib/modules/** rwklx,
  deny /dev/mem rwklx,
  deny /dev/kmem rwklx,
  deny /dev/port rwklx,
  deny /var/run/docker.sock rwklx,
  deny /run/docker.sock rwklx,
  # Confinement: no writing outside the two sanctioned dirs.
  deny /var/lib/** wklx,
  deny /data/** wklx,
  deny /root/** rwklx,
  deny /home/** wklx,
}
