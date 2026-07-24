class K {
  constructor() {
    this.x = 0, this.y = 0, this.zoom = 1, this.targetX = 0, this.targetY = 0, this.smoothing = 0.1;
  }
  setPosition(e, t) {
    this.targetX = e, this.targetY = t;
  }
  snapTo(e, t) {
    this.x = e, this.y = t, this.targetX = e, this.targetY = t;
  }
  update() {
    this.x += (this.targetX - this.x) * this.smoothing, this.y += (this.targetY - this.y) * this.smoothing;
  }
  apply(e) {
    e.setTransform(this.zoom, 0, 0, this.zoom, -this.x * this.zoom, -this.y * this.zoom);
  }
  screenToWorld(e, t) {
    return {
      x: e / this.zoom + this.x,
      y: t / this.zoom + this.y
    };
  }
}
class G {
  constructor(e, t, i, n) {
    this.layers = [], this.animationId = null, this.lastTime = 0, this.scale = n, this.canvas = document.createElement("canvas"), this.canvas.width = t, this.canvas.height = i, this.canvas.style.imageRendering = "pixelated", this.canvas.style.width = `${t * n}px`, this.canvas.style.height = `${i * n}px`;
    const s = this.canvas.getContext("2d");
    if (!s) throw new Error("Could not get 2D context");
    this.ctx = s, this.ctx.imageSmoothingEnabled = !1, this.camera = new K(), e.appendChild(this.canvas);
  }
  addLayer(e) {
    this.layers.push(e), this.layers.sort((t, i) => t.order - i.order);
  }
  removeLayer(e) {
    this.layers = this.layers.filter((t) => t !== e);
  }
  start() {
    this.lastTime = performance.now();
    const e = (t) => {
      const i = (t - this.lastTime) / 1e3;
      this.lastTime = t, this.render(i), this.animationId = requestAnimationFrame(e);
    };
    this.animationId = requestAnimationFrame(e);
  }
  stop() {
    this.animationId !== null && (cancelAnimationFrame(this.animationId), this.animationId = null);
  }
  render(e) {
    const { ctx: t, canvas: i } = this;
    t.setTransform(1, 0, 0, 1, 0, 0), t.clearRect(0, 0, i.width, i.height), this.camera.update(), this.camera.apply(t);
    for (const n of this.layers)
      t.save(), n.render(t, e), t.restore();
  }
  resize(e, t) {
    this.canvas.width = e, this.canvas.height = t, this.canvas.style.width = `${e * this.scale}px`, this.canvas.style.height = `${t * this.scale}px`, this.ctx.imageSmoothingEnabled = !1;
  }
  getScale() {
    return this.scale;
  }
  screenToWorld(e, t) {
    const i = this.canvas.getBoundingClientRect(), n = (e - i.left) / this.scale, s = (t - i.top) / this.scale;
    return this.camera.screenToWorld(n, s);
  }
}
class N {
  constructor(e) {
    this.walkableCache = null, this.grid = e;
  }
  get height() {
    return this.grid.length;
  }
  get width() {
    var e;
    return ((e = this.grid[0]) == null ? void 0 : e.length) ?? 0;
  }
  findPath(e, t, i, n) {
    const s = Math.round(e), a = Math.round(t), r = Math.round(i), o = Math.round(n);
    if (!this.isWalkable(r, o)) return [];
    const h = [], c = /* @__PURE__ */ new Set(), l = { x: s, y: a, g: 0, h: 0, f: 0, parent: null };
    for (l.h = this.heuristic(s, a, r, o), l.f = l.h, h.push(l); h.length > 0; ) {
      h.sort((d, u) => d.f - u.f);
      const p = h.shift(), f = `${p.x},${p.y}`;
      if (p.x === r && p.y === o)
        return this.reconstructPath(p);
      c.add(f);
      for (const [d, u] of [[-1, 0], [1, 0], [0, -1], [0, 1]]) {
        const m = p.x + d, g = p.y + u, y = `${m},${g}`;
        if (!this.isWalkable(m, g) || c.has(y)) continue;
        const w = p.g + 1, T = h.find((b) => b.x === m && b.y === g);
        if (T)
          w < T.g && (T.g = w, T.f = w + T.h, T.parent = p);
        else {
          const b = this.heuristic(m, g, r, o);
          h.push({ x: m, y: g, g: w, h: b, f: w + b, parent: p });
        }
      }
    }
    return [];
  }
  isWalkable(e, t) {
    return e >= 0 && t >= 0 && t < this.height && e < this.width && this.grid[t][e];
  }
  getWalkableTiles() {
    if (this.walkableCache) return this.walkableCache;
    const e = [];
    for (let t = 0; t < this.height; t++)
      for (let i = 0; i < this.width; i++)
        this.grid[t][i] && e.push({ x: i, y: t });
    return this.walkableCache = e, e;
  }
  heuristic(e, t, i, n) {
    return Math.abs(e - i) + Math.abs(t - n);
  }
  reconstructPath(e) {
    const t = [];
    let i = e;
    for (; i; )
      t.unshift({ x: i.x, y: i.y }), i = i.parent;
    return t;
  }
}
const Y = "";
class U {
  constructor(e) {
    this.order = 0, this.tileImages = /* @__PURE__ */ new Map(), this.loaded = !1, this.config = e, this.pathfinder = new N(e.walkable);
  }
  async load(e) {
    const i = Object.entries(this.config.tiles).map(([n, s]) => new Promise((a) => {
      const r = new Image();
      r.onload = () => {
        this.tileImages.set(n, r), a();
      }, r.onerror = () => {
        a();
      };
      const o = /^(\/|blob:|data:|https?:\/\/)/.test(s);
      r.src = o ? s : `${e}/${s}`;
    }));
    await Promise.all(i), this.loaded = !0;
  }
  getLocation(e) {
    return this.config.locations[e];
  }
  getTileImages() {
    return this.tileImages;
  }
  addTile(e, t) {
    this.tileImages.set(e, t), this.config.tiles[e] = "";
  }
  render(e, t) {
    if (!this.loaded) return;
    const { tileWidth: i, tileHeight: n, layers: s } = this.config;
    for (const a of s)
      for (let r = 0; r < a.length; r++)
        for (let o = 0; o < a[r].length; o++) {
          const h = a[r][o];
          if (h === Y) {
            e.fillStyle = "#2a2a2e", e.fillRect(o * i, r * n, i, n);
            continue;
          }
          const c = this.tileImages.get(h);
          c && e.drawImage(
            c,
            0,
            0,
            c.naturalWidth,
            c.naturalHeight,
            o * i,
            r * n,
            i,
            n
          );
        }
  }
}
class _ {
  constructor(e) {
    this.images = /* @__PURE__ */ new Map(), this.loaded = !1, this.config = e;
  }
  async load(e) {
    const t = Object.entries(this.config.sheets).map(([i, n]) => new Promise((s) => {
      const a = new Image();
      a.onload = () => {
        this.images.set(i, a), s();
      }, a.onerror = () => {
        s();
      };
      const r = /^(\/|blob:|data:|https?:\/\/)/.test(n);
      a.src = r ? n : `${e}/${n}`;
    }));
    await Promise.all(t), this.loaded = !0;
  }
  getImage(e) {
    return this.images.get(e);
  }
  isLoaded() {
    return this.loaded;
  }
  drawFrame(e, t, i, n, s) {
    const a = this.config.animations[t];
    if (!a) return;
    const r = this.images.get(a.sheet);
    if (!r) return;
    const { frameWidth: o, frameHeight: h } = this.config, c = i % a.frames * o, l = a.row * h;
    e.drawImage(r, c, l, o, h, n, s, o, h);
  }
}
class X {
  constructor(e, t = "idle_down") {
    this.currentAnimation = "idle_down", this.frame = 0, this.elapsed = 0, this.spriteSheet = e, this.currentAnimation = t;
  }
  play(e) {
    this.currentAnimation !== e && (this.currentAnimation = e, this.frame = 0, this.elapsed = 0);
  }
  getCurrentAnimation() {
    return this.currentAnimation;
  }
  update(e) {
    const t = this.spriteSheet.config.animations[this.currentAnimation];
    t && (this.elapsed += e, this.elapsed >= t.speed && (this.elapsed -= t.speed, this.frame = (this.frame + 1) % t.frames));
  }
  draw(e, t, i) {
    this.spriteSheet.drawFrame(e, this.currentAnimation, this.frame, t, i);
  }
}
class q {
  constructor() {
    this.map = /* @__PURE__ */ new Map();
  }
  key(e, t) {
    return `${e},${t}`;
  }
  reserve(e, t, i) {
    const n = this.key(e, t), s = this.map.get(n);
    return s && s !== i ? !1 : (this.map.set(n, i), !0);
  }
  release(e) {
    for (const [t, i] of this.map)
      i === e && this.map.delete(t);
  }
  isAvailable(e, t, i) {
    const n = this.map.get(this.key(e, t));
    return !n || n === i;
  }
}
const B = {
  working: "working",
  idle: "idle_down",
  thinking: "idle_down",
  error: "idle_down",
  waiting: "idle_down",
  collaborating: "walk_down",
  sleeping: "sleeping",
  listening: "idle_down",
  speaking: "talking",
  offline: "idle_down"
};
class D {
  constructor(e, t, i, n) {
    this.x = 0, this.y = 0, this.state = "idle", this.task = null, this.energy = 1, this.visible = !0, this.separationX = 0, this.separationY = 0, this.path = [], this.pathIndex = 0, this.moveSpeed = 2, this.moveProgress = 0, this.homePosition = "", this.tileWidth = 16, this.tileHeight = 16, this.idleBehaviorTimer = 0, this.idleBehaviorInterval = 5 + Math.random() * 5, this.currentAnchor = null, this.npcPhase = "idle", this.npcPhaseTimer = 0, this.npcPhaseDuration = 0, this.agentId = e.agentId, this.name = e.name, this.spriteSheet = t, this.animator = new X(t), this.homePosition = e.position, this.tileWidth = i, this.tileHeight = n, this.frameWidth = t.config.frameWidth, this.frameHeight = t.config.frameHeight, this.isNpc = e.npc ?? !1, this.isNpc && (this.npcPhase = "idle", this.npcPhaseDuration = 3 + Math.random() * 5);
  }
  getHomePosition() {
    return this.homePosition;
  }
  setHomePosition(e) {
    this.homePosition = e;
  }
  setPixelPosition(e, t) {
    this.x = e, this.y = t;
  }
  setTilePosition(e, t) {
    this.x = e * this.tileWidth, this.y = t * this.tileHeight;
  }
  getTilePosition() {
    return {
      x: Math.round(this.x / this.tileWidth),
      y: Math.round(this.y / this.tileHeight)
    };
  }
  walkTo(e) {
    e.length <= 1 || (this.path = e, this.pathIndex = 1, this.moveProgress = 0);
  }
  isMoving() {
    return this.pathIndex < this.path.length;
  }
  updateState(e, t, i) {
    const n = this.state;
    if (this.state = e, this.task = t, this.energy = i, this.visible = e !== "offline", n !== e && !this.isMoving()) {
      const s = B[e] ?? "idle_down";
      this.animator.play(s);
    }
  }
  faceDirection(e) {
    const i = `${this.state === "idle" ? "idle" : "walk"}_${e}`;
    this.spriteSheet.config.animations[i] && this.animator.play(i);
  }
  update(e, t, i, n, s, a) {
    if (this.isNpc && !this.isMoving() && this.updateNpcPhase(e, t, n, s, a), this.isMoving())
      this.updateMovement(e);
    else if (this.state === "idle")
      this.updateIdleBehavior(e, t, i, n, s, a);
    else {
      const r = B[this.state] ?? "idle_down";
      this.animator.getCurrentAnimation() !== r && this.animator.play(r);
    }
    this.animator.update(e);
  }
  /** NPC phase cycling: idle/wander → working → idle/wander → resting → repeat */
  updateNpcPhase(e, t, i, n, s) {
    if (this.npcPhaseTimer += e, this.npcPhaseTimer < this.npcPhaseDuration) return;
    this.npcPhaseTimer = 0, this.npcPhase, this.npcPhase === "idle" ? (this.npcPhase = Math.random() < 0.6 ? "working" : "resting", this.npcPhaseDuration = 10 + Math.random() * 20) : (this.npcPhase = "idle", this.npcPhaseDuration = 5 + Math.random() * 10);
    const a = this.npcPhase === "working" ? "working" : this.npcPhase === "resting" ? "sleeping" : "idle";
    if (a !== this.state) {
      let r = !1;
      if (i && i.length > 0)
        if (a === "working") {
          const o = this.getHomePosition();
          r = this.goToAnchor(o, i, t, n) || this.goToAnchorType("work", i, t, n, s);
        } else a === "sleeping" && (r = this.goToAnchorType("rest", i, t, n, s));
      a === "idle" || r ? (this.updateState(a, null, this.energy), a === "idle" && (this.idleBehaviorTimer = this.idleBehaviorInterval)) : (this.npcPhase = "idle", this.npcPhaseDuration = 3 + Math.random() * 5);
    }
  }
  updateMovement(e) {
    if (this.pathIndex >= this.path.length) return;
    const t = this.path[this.pathIndex], i = t.x * this.tileWidth, n = t.y * this.tileHeight, s = i - this.x, a = n - this.y;
    if (Math.abs(s) > Math.abs(a) ? this.animator.play(s > 0 ? "walk_right" : "walk_left") : this.animator.play(a > 0 ? "walk_down" : "walk_up"), this.moveProgress += e * this.moveSpeed, this.moveProgress >= 1) {
      if (this.x = i, this.y = n, this.moveProgress = 0, this.pathIndex++, this.pathIndex >= this.path.length) {
        this.path = [], this.pathIndex = 0;
        const r = B[this.state] ?? "idle_down";
        this.animator.play(r);
      }
    } else {
      const r = this.path[this.pathIndex - 1], o = r.x * this.tileWidth, h = r.y * this.tileHeight;
      this.x = o + (i - o) * this.moveProgress, this.y = h + (n - h) * this.moveProgress;
    }
  }
  /** Navigate to a specific anchor by name */
  goToAnchor(e, t, i, n) {
    const s = t.find((o) => o.name === e);
    if (!s || n && !n.isAvailable(s.x, s.y, this.agentId)) return !1;
    const a = this.getTilePosition();
    if (a.x === s.x && a.y === s.y)
      return n && (n.release(this.agentId), n.reserve(s.x, s.y, this.agentId)), this.currentAnchor = s.name, !0;
    const r = i.findPath(a.x, a.y, s.x, s.y);
    return r.length > 1 ? (n && (n.release(this.agentId), n.reserve(s.x, s.y, this.agentId)), this.currentAnchor = s.name, this.walkTo(r), !0) : !1;
  }
  /** Navigate to a specific anchor by type, respecting reservation */
  goToAnchorType(e, t, i, n, s) {
    const a = t.filter(
      (h) => h.type === e && (!s || !s.has(h.name))
    );
    if (a.length === 0) return !1;
    const r = [...a].sort(() => Math.random() - 0.5), o = this.getTilePosition();
    for (const h of r) {
      if (n && !n.isAvailable(h.x, h.y, this.agentId)) continue;
      if (o.x === h.x && o.y === h.y)
        return n && (n.release(this.agentId), n.reserve(h.x, h.y, this.agentId)), this.currentAnchor = h.name, !0;
      const c = i.findPath(o.x, o.y, h.x, h.y);
      if (c.length > 1)
        return n && (n.release(this.agentId), n.reserve(h.x, h.y, this.agentId)), this.currentAnchor = h.name, this.walkTo(c), !0;
    }
    return !1;
  }
  getCurrentAnchor() {
    return this.currentAnchor;
  }
  updateIdleBehavior(e, t, i, n, s, a) {
    if (this.idleBehaviorTimer += e, this.idleBehaviorTimer < this.idleBehaviorInterval) return;
    if (this.idleBehaviorTimer = 0, this.idleBehaviorInterval = 5 + Math.random() * 8, n && n.length > 0) {
      const l = ["wander", "social", "utility"].filter(
        (p) => n.some((f) => f.type === p && (!a || !a.has(f.name)))
      ).sort(() => Math.random() - 0.5);
      for (const p of l)
        if (this.goToAnchorType(p, n, t, s, a)) return;
    }
    const r = Object.keys(i).sort(() => Math.random() - 0.5), o = this.getTilePosition();
    for (const h of r) {
      const c = i[h];
      if (s && !s.isAvailable(c.x, c.y, this.agentId)) continue;
      const l = t.findPath(o.x, o.y, c.x, c.y);
      if (l.length > 1) {
        s && (s.release(this.agentId), s.reserve(c.x, c.y, this.agentId)), this.walkTo(l);
        return;
      }
    }
    this.walkToRandomTile(t, s);
  }
  /** Pick a random walkable tile and walk there */
  walkToRandomTile(e, t) {
    const i = this.getTilePosition(), n = e.getWalkableTiles();
    if (n.length === 0) return;
    const s = Math.min(10, n.length);
    for (let a = 0; a < s; a++) {
      const r = Math.floor(Math.random() * n.length), o = n[r];
      if (Math.abs(o.x - i.x) + Math.abs(o.y - i.y) < 2 || t && !t.isAvailable(o.x, o.y, this.agentId)) continue;
      const h = e.findPath(i.x, i.y, o.x, o.y);
      if (h.length > 1) {
        t && (t.release(this.agentId), t.reserve(o.x, o.y, this.agentId)), this.walkTo(h);
        return;
      }
    }
  }
  /** Y offset applied when the character is sitting (working/sleeping) */
  getSittingOffset() {
    return this.state === "working" || this.state === "sleeping" ? this.tileHeight * 1.2 : 0;
  }
  /** Whether this citizen is anchored (sitting) and should not be pushed by separation */
  isAnchored() {
    return this.state === "working" || this.state === "sleeping";
  }
  /**
   * Apply separation steering: push away from nearby citizens.
   * Call once per frame from the update loop, passing all other citizens.
   */
  applySeparation(e, t) {
    if (this.isAnchored() || !this.visible) return;
    const i = this.tileWidth * 1.5;
    let n = 0, s = 0;
    for (const h of e) {
      if (h === this || !h.visible) continue;
      const c = this.x - h.x, l = this.y - h.y, p = Math.sqrt(c * c + l * l);
      if (p < i && p > 0.01) {
        const f = (i - p) / i;
        n += c / p * f, s += l / p * f;
      } else if (p <= 0.01) {
        const f = Math.random() * Math.PI * 2;
        n += Math.cos(f) * 0.5, s += Math.sin(f) * 0.5;
      }
    }
    const a = 60 * t;
    this.separationX += n * a, this.separationY += s * a;
    const r = 0.9;
    this.separationX *= r, this.separationY *= r;
    const o = this.tileWidth * 0.5;
    this.separationX = Math.max(-o, Math.min(o, this.separationX)), this.separationY = Math.max(-o, Math.min(o, this.separationY));
  }
  draw(e) {
    if (!this.visible) return;
    const t = this.isAnchored() ? 0 : this.separationX, i = this.isAnchored() ? 0 : this.separationY, n = this.x + (this.tileWidth - this.frameWidth) / 2 + t, s = this.y + (this.tileHeight - this.frameHeight) - this.getSittingOffset() + i;
    this.animator.draw(e, n, s);
  }
  containsPoint(e, t) {
    const i = this.isAnchored() ? 0 : this.separationX, n = this.isAnchored() ? 0 : this.separationY, s = this.x + (this.tileWidth - this.frameWidth) / 2 + i, a = this.y + (this.tileHeight - this.frameHeight) + n;
    return e >= s && e <= s + this.frameWidth && t >= a && t <= a + this.frameHeight;
  }
}
class ie {
  constructor() {
    this.order = 12, this.citizens = [];
  }
  setCitizens(e) {
    this.citizens = e;
  }
  render(e, t) {
    const i = this.citizens.filter((n) => n.visible && (n.state === "working" || n.state === "sleeping")).sort((n, s) => n.y - s.y);
    for (const n of i)
      n.draw(e);
  }
}
class se {
  constructor() {
    this.order = 20, this.citizens = [];
  }
  setCitizens(e) {
    this.citizens = e;
  }
  render(e, t) {
    const i = this.citizens.filter((n) => n.visible && n.state !== "working" && n.state !== "sleeping").sort((n, s) => n.y - s.y);
    for (const n of i)
      n.draw(e);
  }
}
class J {
  constructor() {
    this.order = 10, this.below = new ie(), this.above = new se();
  }
  setCitizens(e) {
    this.below.setCitizens(e), this.above.setCitizens(e);
  }
  getLayers() {
    return [this.below, this.above];
  }
  render(e, t) {
  }
}
class Z {
  constructor(e) {
    this.active = !1, this.shakeTimer = 0, this.glowing = !1, this.displayText = "", this.config = e;
  }
  activate() {
    this.active = !0, this.shakeTimer = 1;
  }
  deactivate() {
    this.active = !1, this.shakeTimer = 0;
  }
  setGlow(e) {
    this.glowing = e;
  }
  setText(e) {
    this.displayText = e;
  }
  isActive() {
    return this.active;
  }
  containsPoint(e, t) {
    const { x: i, y: n, width: s, height: a } = this.config;
    return e >= i && e <= i + s && t >= n && t <= n + a;
  }
  update(e) {
    this.shakeTimer > 0 && (this.shakeTimer -= e, this.shakeTimer <= 0 && (this.active = !1));
  }
  draw(e) {
    const { x: t, y: i, width: n, height: s, type: a } = this.config;
    let r = t;
    const o = i;
    switch (this.shakeTimer > 0 && (r += Math.sin(this.shakeTimer * 30) * 1), this.glowing && (e.save(), e.shadowColor = "#66aaff", e.shadowBlur = 4, e.fillStyle = "rgba(100, 170, 255, 0.15)", e.fillRect(r - 1, o - 1, n + 2, s + 2), e.restore()), e.save(), a) {
      case "intercom":
        e.fillStyle = "#666666", e.fillRect(r, o, n, s), e.fillStyle = "#aaaaaa", e.fillRect(r + 1, o + 1, n - 2, s - 2), this.active && (e.fillStyle = "#ff4444", e.beginPath(), e.arc(r + n / 2, o + 4, 3, 0, Math.PI * 2), e.fill());
        break;
      case "whiteboard":
        e.fillStyle = "#eeeeee", e.fillRect(r, o, n, s), e.strokeStyle = "#999999", e.lineWidth = 0.5, e.strokeRect(r, o, n, s), this.displayText && (e.fillStyle = "#333333", e.font = "8px monospace", e.fillText(this.displayText.substring(0, 20), r + 4, o + s / 2 + 2));
        break;
      case "coffee_machine":
        e.fillStyle = "#8B4513", e.fillRect(r, o, n, s), e.fillStyle = "#654321", e.fillRect(r + 2, o + 2, n - 4, s - 4);
        break;
      default:
        e.fillStyle = "#888888", e.fillRect(r, o, n, s);
        break;
    }
    e.restore();
  }
}
class V {
  constructor() {
    this.order = 20, this.particles = [];
  }
  emitZzz(e, t) {
    this.particles.push({
      x: e + Math.random() * 16,
      y: t,
      vx: 0.3 + Math.random() * 0.4,
      vy: -0.8 - Math.random() * 0.4,
      life: 2,
      maxLife: 2,
      text: "Z",
      size: 10 + Math.random() * 6,
      alpha: 1
    });
  }
  emitExclamation(e, t) {
    this.particles.push({
      x: e,
      y: t - 8,
      vx: 0,
      vy: -0.4,
      life: 1.5,
      maxLife: 1.5,
      text: "!",
      size: 14,
      alpha: 1
    });
  }
  emitThought(e, t) {
    this.particles.push({
      x: e + 12,
      y: t - 4,
      vx: 0,
      vy: -0.3,
      life: 2,
      maxLife: 2,
      text: "...",
      size: 10,
      alpha: 1
    });
  }
  update(e) {
    for (const t of this.particles)
      t.x += t.vx * e * 10, t.y += t.vy * e * 10, t.life -= e, t.alpha = Math.max(0, t.life / t.maxLife);
    this.particles = this.particles.filter((t) => t.life > 0);
  }
  render(e, t) {
    this.update(t);
    for (const i of this.particles)
      e.save(), e.globalAlpha = i.alpha, e.fillStyle = "#ffffff", e.strokeStyle = "#000000", e.lineWidth = 0.5, e.font = `bold ${i.size}px monospace`, e.strokeText(i.text, i.x, i.y), e.fillText(i.text, i.x, i.y), e.restore();
  }
}
class Q {
  constructor() {
    this.order = 25, this.bubbles = [];
  }
  show(e, t, i, n = 3, s) {
    s ? this.bubbles = this.bubbles.filter((o) => o.target !== s) : this.bubbles = this.bubbles.filter((o) => !(Math.abs(o.x - e) < 1 && Math.abs(o.y - t) < 1));
    const a = s ? e - s.x : 0, r = s ? t - s.y : 0;
    this.bubbles.push({ x: e, y: t, text: i, life: n, maxLife: n, target: s, offsetX: a, offsetY: r });
  }
  clear() {
    this.bubbles = [];
  }
  render(e, t) {
    for (const i of this.bubbles)
      i.life -= t;
    this.bubbles = this.bubbles.filter((i) => i.life > 0);
    for (const i of this.bubbles) {
      i.target && (i.x = i.target.x + i.offsetX, i.y = i.target.y + i.offsetY - i.target.getSittingOffset());
      const n = Math.min(1, i.life / 0.5);
      e.save(), e.globalAlpha = n, e.font = "9px monospace";
      const s = e.measureText(i.text), a = Math.min(s.width, 120), r = 6, o = a + r * 2, h = 18, c = i.x - o / 2, l = i.y - h - 8;
      e.fillStyle = "#ffffff", e.strokeStyle = "#333333", e.lineWidth = 1, e.beginPath(), e.roundRect(c, l, o, h, 4), e.fill(), e.stroke(), e.beginPath(), e.moveTo(i.x - 4, l + h), e.lineTo(i.x, l + h + 6), e.lineTo(i.x + 4, l + h), e.fill(), e.fillStyle = "#333333", e.fillText(i.text.substring(0, 20), c + r, l + h - 5), e.restore();
    }
  }
}
function j(x) {
  return x.map((e) => ({
    id: e.id ?? e.agent,
    name: e.name ?? e.id ?? e.agent,
    state: e.state ?? "idle",
    task: e.task ?? null,
    energy: e.energy ?? 1,
    metadata: e.metadata
  }));
}
class ee {
  constructor(e) {
    this.callbacks = [], this.eventCallbacks = [], this.messageCallbacks = [], this.intervalId = null, this.ws = null, this.config = e;
  }
  onUpdate(e) {
    this.callbacks.push(e);
  }
  /** Register callback for world events (interactive mode) */
  onEvent(e) {
    this.eventCallbacks.push(e);
  }
  /** Register callback for direct/channel messages */
  onMessage(e) {
    this.messageCallbacks.push(e);
  }
  /** Send an action to the server (interactive mode, WebSocket only) */
  sendAction(e, t) {
    this.ws && this.ws.readyState === WebSocket.OPEN && this.ws.send(JSON.stringify({ type: "action", agent: e, action: t }));
  }
  /** Request a world snapshot (interactive mode, WebSocket only) */
  requestObserve(e, t) {
    this.ws && this.ws.readyState === WebSocket.OPEN && this.ws.send(JSON.stringify({ type: "observe", agent: e, since: t }));
  }
  emit(e) {
    for (const t of this.callbacks)
      t(e);
  }
  emitEvent(e) {
    for (const t of this.eventCallbacks)
      t(e);
  }
  emitMessage(e) {
    for (const t of this.messageCallbacks)
      t(e);
  }
  start() {
    switch (this.config.type) {
      case "rest":
        this.startPolling();
        break;
      case "websocket":
        this.startWebSocket();
        break;
      case "mock":
        this.startMock();
        break;
    }
  }
  stop() {
    this.intervalId && (clearInterval(this.intervalId), this.intervalId = null), this.ws && (this.ws.close(), this.ws = null);
  }
  async startPolling() {
    const e = this.config.url, t = this.config.interval ?? 3e3, i = async () => {
      try {
        const s = await (await fetch(e)).json();
        this.emit(s.agents ?? []);
      } catch {
      }
    };
    await i(), this.intervalId = setInterval(i, t);
  }
  startWebSocket() {
    const e = this.config.url;
    this.ws = new WebSocket(e), this.ws.onmessage = (t) => {
      try {
        const i = JSON.parse(t.data);
        if (i.type === "agents")
          this.emit(j(i.agents ?? []));
        else if (i.type === "event" && i.event)
          this.emitEvent(i.event);
        else if (i.type === "message" && i.from && i.message)
          this.emitMessage({ from: i.from, message: i.message, channel: i.channel });
        else if (i.type === "world" && i.snapshot && (i.snapshot.agents && this.emit(j(i.snapshot.agents)), i.snapshot.events))
          for (const n of i.snapshot.events) this.emitEvent(n);
      } catch {
      }
    }, this.ws.onclose = () => {
      setTimeout(() => {
        this.ws && this.startWebSocket();
      }, 5e3);
    };
  }
  startMock() {
    if (!this.config.mockData) return;
    const e = this.config.interval ?? 3e3;
    this.emit(this.config.mockData()), this.intervalId = setInterval(() => {
      this.emit(this.config.mockData());
    }, e);
  }
}
const $ = ["work", "rest", "social", "utility", "wander"], M = {
  work: "#4ade80",
  rest: "#818cf8",
  social: "#fbbf24",
  utility: "#22d3ee",
  wander: "#888888"
}, H = {
  desk: [{ ox: 0.5, oy: -1, type: "work" }],
  chair: [],
  couch: [{ ox: 0.5, oy: 0, type: "rest" }, { ox: 1.5, oy: 0, type: "rest" }],
  coffee_machine: [{ ox: 0.5, oy: 1.8, type: "utility" }],
  whiteboard: [{ ox: 1, oy: 1.5, type: "social" }],
  bookshelf: [],
  water_cooler: [{ ox: 0, oy: 1.8, type: "utility" }],
  plant: [],
  lamp: []
};
function ne(x) {
  if (H[x]) return H[x];
  for (const [e, t] of Object.entries(H))
    if (t.length > 0 && x.includes(e)) return t;
}
function F(x, e) {
  const t = ne(x.id);
  return !t || t.length === 0 ? [] : t.map((i, n) => ({
    ...i,
    // Negative oy means "below the piece" — resolve to piece.h + |oy| - 1
    oy: i.oy < 0 ? x.h + Math.abs(i.oy) - 1 : i.oy,
    name: `${x.id}_${e}_${n}`
  }));
}
class oe {
  constructor(e, t) {
    this.pieces = [], this.selected = /* @__PURE__ */ new Set(), this.images = /* @__PURE__ */ new Map(), this.imageSrcs = /* @__PURE__ */ new Map(), this.dragging = !1, this.dragOffsets = /* @__PURE__ */ new Map(), this.clipboard = [], this.onSaveCallback = null, this.deadspaceCheck = null, this.tileSize = e, this.scale = t, this.wanderPoints = [
      { name: "wander_center", x: 7, y: 6 },
      { name: "wander_lounge", x: 5, y: 8 }
    ];
  }
  async loadSprite(e, t) {
    const i = await new Promise((n, s) => {
      const a = new Image();
      a.onload = () => n(a), a.onerror = () => s(new Error(`Failed to load sprite: ${t}`)), a.src = t;
    });
    this.images.set(e, i), this.imageSrcs.set(e, t);
  }
  getImageSrcs() {
    return this.imageSrcs;
  }
  getTileSize() {
    return this.tileSize;
  }
  getScale() {
    return this.scale;
  }
  setLayout(e) {
    this.pieces = e.map((t, i) => ({
      ...t,
      img: this.images.get(t.id),
      anchors: t.anchors ?? F(t, i)
    })).filter((t) => t.img);
  }
  getLayout() {
    return this.pieces.map(({ id: e, x: t, y: i, w: n, h: s, layer: a, anchors: r }) => ({
      id: e,
      x: t,
      y: i,
      w: n,
      h: s,
      layer: a,
      anchors: r.length > 0 ? r : void 0
    }));
  }
  getLocations() {
    const e = [];
    for (const t of this.pieces)
      for (const i of t.anchors)
        e.push({
          name: i.name,
          x: Math.round(t.x + i.ox),
          y: Math.round(t.y + i.oy),
          type: i.type
        });
    for (const t of this.wanderPoints)
      e.push({ name: t.name, x: t.x, y: t.y, type: "wander" });
    return e;
  }
  getLocationMap() {
    const e = {};
    for (const t of this.getLocations())
      e[t.name] = { x: t.x, y: t.y, label: t.name };
    return e;
  }
  onSave(e) {
    this.onSaveCallback = e;
  }
  setDeadspaceCheck(e) {
    this.deadspaceCheck = e;
  }
  occupiesTile(e, t) {
    for (const i of this.pieces)
      if (e >= Math.floor(i.x) && e < Math.ceil(i.x + i.w) && t >= Math.floor(i.y) && t < Math.ceil(i.y + i.h))
        return !0;
    return !1;
  }
  overlapsDeadspace(e, t, i, n) {
    if (!this.deadspaceCheck) return !1;
    const s = Math.floor(e), a = Math.floor(t), r = Math.ceil(e + i), o = Math.ceil(t + n);
    for (let h = a; h < o; h++)
      for (let c = s; c < r; c++)
        if (this.deadspaceCheck(c, h)) return !0;
    return !1;
  }
  getBlockedTiles() {
    const e = /* @__PURE__ */ new Set();
    for (const t of this.pieces) {
      const i = Math.floor(t.x), n = Math.floor(t.y), s = Math.ceil(t.x + t.w), a = Math.ceil(t.y + t.h);
      for (let r = n; r < a; r++)
        for (let o = i; o < s; o++)
          e.add(`${o},${r}`);
    }
    return e;
  }
  setWanderPoints(e) {
    this.wanderPoints = e;
  }
  save() {
    var e;
    console.log("[props] Layout updated"), (e = this.onSaveCallback) == null || e.call(this);
  }
  addPiece(e) {
    const t = this.images.get(e);
    if (!t) return null;
    const i = t.naturalWidth / t.naturalHeight, n = 2, s = Math.round(n * i * 10) / 10;
    let a = 6, r = 5;
    if (this.overlapsDeadspace(a, r, s, n)) {
      let c = !1;
      for (let l = 1; l < 20 && !c; l++)
        for (let p = 1; p < 20 && !c; p++)
          this.overlapsDeadspace(p, l, s, n) || (a = p, r = l, c = !0);
    }
    const o = this.pieces.length, h = {
      id: e,
      img: t,
      x: a,
      y: r,
      w: s,
      h: n,
      layer: e === "chair" ? "above" : "below",
      anchors: F({ id: e, h: n }, o)
    };
    return this.pieces.push(h), h;
  }
  removePiece(e) {
    this.pieces = this.pieces.filter((t) => t !== e), this.selected.delete(e);
  }
  // --- Rendering ---
  renderBelow(e) {
    e.imageSmoothingEnabled = !1;
    const t = this.tileSize;
    for (const i of this.pieces)
      i.layer === "below" && e.drawImage(i.img, i.x * t, i.y * t, i.w * t, i.h * t);
  }
  renderAbove(e) {
    e.imageSmoothingEnabled = !1;
    const t = this.tileSize;
    for (const i of this.pieces)
      i.layer === "above" && e.drawImage(i.img, i.x * t, i.y * t, i.w * t, i.h * t);
  }
  // --- Mouse interaction (world pixel coords) ---
  handleMouseDown(e, t, i = !1) {
    const n = this.pieceAt(e, t);
    if (n) {
      i ? this.selected.has(n) ? this.selected.delete(n) : this.selected.add(n) : this.selected.has(n) || (this.selected.clear(), this.selected.add(n)), this.dragging = !0, this.dragOffsets.clear();
      for (const s of this.selected)
        this.dragOffsets.set(s, {
          dx: e - s.x * this.tileSize,
          dy: t - s.y * this.tileSize
        });
      return !0;
    }
    return i || this.selected.clear(), !1;
  }
  handleMouseMove(e, t) {
    if (!this.dragging || this.selected.size === 0) return;
    const i = this.tileSize, n = [];
    for (const s of this.selected) {
      const a = this.dragOffsets.get(s);
      if (!a) continue;
      const r = this.snap((e - a.dx) / i), o = this.snap((t - a.dy) / i);
      if (this.overlapsDeadspace(r, o, s.w, s.h)) return;
      n.push({ piece: s, nx: r, ny: o });
    }
    for (const s of n)
      s.piece.x = s.nx, s.piece.y = s.ny;
  }
  handleMouseUp() {
    this.dragging = !1;
  }
  handleKey(e) {
    if ((e.metaKey || e.ctrlKey) && e.key === "c" && this.selected.size > 0)
      return this.clipboard = [...this.selected].map((t) => ({
        id: t.id,
        w: t.w,
        h: t.h,
        layer: t.layer,
        anchors: t.anchors.map((i) => ({ ...i }))
      })), !0;
    if ((e.metaKey || e.ctrlKey) && e.key === "v" && this.clipboard.length > 0) {
      this.selected.clear();
      for (const t of this.clipboard) {
        const i = this.images.get(t.id);
        if (!i) continue;
        const n = this.pieces.length, s = {
          id: t.id,
          img: i,
          x: 4 + Math.random() * 2,
          y: 4 + Math.random() * 2,
          w: t.w,
          h: t.h,
          layer: t.layer,
          anchors: t.anchors.map((a, r) => ({
            ...a,
            name: `${t.id}_${n}_${r}`
          }))
        };
        this.pieces.push(s), this.selected.add(s);
      }
      return !0;
    }
    if (this.selected.size === 0) return !1;
    if (e.key === "Delete" || e.key === "Backspace") {
      for (const t of this.selected)
        this.pieces = this.pieces.filter((i) => i !== t);
      return this.selected.clear(), !0;
    }
    if (e.key === "l" || e.key === "L") {
      for (const t of this.selected)
        t.layer = t.layer === "below" ? "above" : "below";
      return !0;
    }
    if (e.key.startsWith("Arrow")) {
      const t = e.shiftKey ? 1 : 0.25;
      let i = 0, n = 0;
      e.key === "ArrowLeft" && (i = -t), e.key === "ArrowRight" && (i = t), e.key === "ArrowUp" && (n = -t), e.key === "ArrowDown" && (n = t);
      for (const s of this.selected)
        if (this.overlapsDeadspace(s.x + i, s.y + n, s.w, s.h)) return !0;
      for (const s of this.selected)
        s.x += i, s.y += n;
      return e.preventDefault(), !0;
    }
    if (e.key === "=" || e.key === "+") {
      for (const t of this.selected)
        t.w += 0.1, t.h += 0.1;
      return !0;
    }
    if (e.key === "-") {
      for (const t of this.selected)
        t.w = Math.max(0.5, t.w - 0.1), t.h = Math.max(0.5, t.h - 0.1);
      return !0;
    }
    return !1;
  }
  // --- Helpers ---
  pieceAt(e, t) {
    const i = this.tileSize;
    for (let n = this.pieces.length - 1; n >= 0; n--) {
      const s = this.pieces[n], a = s.x * i, r = s.y * i, o = s.w * i, h = s.h * i;
      if (e >= a && e <= a + o && t >= r && t <= r + h)
        return s;
    }
    return null;
  }
  snap(e) {
    return Math.round(e * 4) / 4;
  }
}
class ae {
  constructor(e) {
    this.active = !1, this.tab = "world", this.wrapper = null, this.panel = null, this.tabBtns = /* @__PURE__ */ new Map(), this.tabContent = null, this.undoStack = [], this.redoStack = [], this.maxHistory = 50, this.preActionSnapshot = null, this.selectedCitizenId = null, this.citizenTypes = /* @__PURE__ */ new Map(), this.citizenSprites = /* @__PURE__ */ new Map(), this.selAnchorPiece = null, this.selAnchorIdx = -1, this.draggingAnchor = !1, this.dragAnchorOx = 0, this.dragAnchorOy = 0, this.genType = "props", this.genStatus = "", this.genPreview = null, this.genBusy = !1, this.selectedTileKey = "", this.propsInfo = null, this.citizensInfo = null, this.citizensList = null, this.citizensBuiltFor = null, this.behaviorInfo = null, this.painting = !1, this.gridLabel = null, this.canvas = e.canvas, this.scale = e.props.getScale(), this.tileSize = e.props.getTileSize(), this.props = e.props, this.mv = e.miniverse, this.worldId = e.worldId ?? "", this.saveFn = e.onSave ?? null, this.apiBase = e.apiBase ?? "", this.onMouseDown = this.onMouseDown.bind(this), this.onMouseMove = this.onMouseMove.bind(this), this.onMouseUp = this.onMouseUp.bind(this), this.onKeyDown = this.onKeyDown.bind(this), window.addEventListener("keydown", this.onKeyDown);
  }
  isActive() {
    return this.active;
  }
  getTab() {
    return this.tab;
  }
  // --- Rendering (called from addLayer) ---
  renderOverlay(e) {
    if (this.active) {
      switch (e.save(), this.renderGrid(e), this.tab) {
        case "world":
          this.renderWorldOverlay(e);
          break;
        case "props":
          this.renderPropsOverlay(e);
          break;
        case "citizens":
          this.renderCitizensOverlay(e);
          break;
        case "behavior":
          this.renderBehaviorOverlay(e);
          break;
      }
      e.restore();
    }
  }
  // --- Grid (shared across tabs) ---
  renderGrid(e) {
    const t = this.tileSize;
    e.strokeStyle = "rgba(255,255,255,0.12)", e.lineWidth = 0.5;
    const i = e.canvas.width / this.scale, n = e.canvas.height / this.scale;
    for (let s = 0; s <= i; s += t)
      e.beginPath(), e.moveTo(s, 0), e.lineTo(s, n), e.stroke();
    for (let s = 0; s <= n; s += t)
      e.beginPath(), e.moveTo(0, s), e.lineTo(i, s), e.stroke();
  }
  /** @deprecated No longer needed — tileMap keys are the names. */
  setTileNames(e) {
  }
  renderWorldOverlay(e) {
    const t = this.tileSize, i = this.mv.getFloorLayer();
    if (i)
      for (let n = 0; n < i.length; n++)
        for (let s = 0; s < i[n].length; s++)
          i[n][s] === "" && (e.fillStyle = "rgba(0,0,0,0.85)", e.fillRect(s * t, n * t, t, t), e.strokeStyle = "rgba(255,50,50,0.4)", e.lineWidth = 1, e.beginPath(), e.moveTo(s * t + 4, n * t + 4), e.lineTo((s + 1) * t - 4, (n + 1) * t - 4), e.moveTo((s + 1) * t - 4, n * t + 4), e.lineTo(s * t + 4, (n + 1) * t - 4), e.stroke());
  }
  buildWorldTab() {
    const e = this.tabContent, t = this.el("div", "padding:6px 10px; border-bottom:1px solid #333; line-height:1.6;");
    t.innerHTML = [
      '<span style="color:#00ff88">Click</span> paint tile',
      '<span style="color:#00ff88">Drag</span> paint area'
    ].join("<br>"), e.appendChild(t);
    const i = this.el("div", "padding:6px 10px; border-bottom:1px solid #333; display:flex; align-items:center; gap:6px;"), { cols: n, rows: s } = this.mv.getGridSize();
    this.gridLabel = this.el("span", "flex:1; color:#888; font-size:10px;"), this.gridLabel.textContent = `Grid: ${n}×${s}`, i.appendChild(this.gridLabel), i.appendChild(this.makeBtn("+C", () => {
      this.beginAction(), this.resizeGrid(1, 0), this.commitAction();
    })), i.appendChild(this.makeBtn("-C", () => {
      this.beginAction(), this.resizeGrid(-1, 0), this.commitAction();
    })), i.appendChild(this.makeBtn("+R", () => {
      this.beginAction(), this.resizeGrid(0, 1), this.commitAction();
    })), i.appendChild(this.makeBtn("-R", () => {
      this.beginAction(), this.resizeGrid(0, -1), this.commitAction();
    })), e.appendChild(i);
    const a = this.el("div", "padding:4px 10px; color:#555; font-size:9px; text-transform:uppercase; letter-spacing:1px;");
    a.textContent = "Tiles", e.appendChild(a);
    const r = this.el("div", "padding:4px 8px; display:flex; flex-wrap:wrap; gap:4px;"), o = this.el("div", `
      width:40px; height:40px; border:2px solid ${this.selectedTileKey === "" ? "#00ff88" : "#333"}; border-radius:3px;
      cursor:pointer; background:#0a0a0a; overflow:hidden; position:relative;
      display:flex; align-items:center; justify-content:center;
    `);
    o.title = "Deadspace (void)";
    const h = this.el("span", "font-size:20px; opacity:0.6; user-select:none;");
    h.textContent = "☠", o.appendChild(h), o.addEventListener("click", () => {
      this.selectedTileKey = "", this.buildTabContent();
    }), r.appendChild(o);
    const c = this.mv.getTileImages(), l = this.mv.getTiles();
    for (const f of Object.keys(l)) {
      const d = c.get(f), u = this.el("div", `
        width:40px; height:40px; border:2px solid ${f === this.selectedTileKey ? "#00ff88" : "#333"}; border-radius:3px;
        cursor:pointer; background:#1a1a2e; overflow:hidden; position:relative;
      `);
      if (u.title = f, d) {
        const g = document.createElement("canvas");
        g.width = 32, g.height = 32, g.style.cssText = "width:36px; height:36px; image-rendering:pixelated;";
        const y = g.getContext("2d");
        y.imageSmoothingEnabled = !1, y.drawImage(d, 0, 0, d.naturalWidth, d.naturalHeight, 0, 0, 32, 32), u.appendChild(g);
      }
      const m = this.el("div", "position:absolute; bottom:0; left:1px; right:1px; font-size:7px; color:#888; text-align:center; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;");
      m.textContent = f, u.appendChild(m), u.addEventListener("click", () => {
        this.selectedTileKey = f, this.buildTabContent();
      }), r.appendChild(u);
    }
    e.appendChild(r);
    const p = this.worldId ? `?worldId=${this.worldId}` : "";
    fetch(`${this.apiBase}/api/tiles${p}`).then((f) => f.json()).then((f) => {
      for (const d of f) {
        if (l[d]) continue;
        const m = `${this.worldId ? `/worlds/${this.worldId}/world_assets/tiles` : "/universal_assets/tiles"}/${d}.png`, g = new Image();
        g.onload = () => {
          this.mv.addTile(d, g, m);
          const y = this.el("div", `
            width:40px; height:40px; border:2px solid #333; border-radius:3px;
            cursor:pointer; background:#1a1a2e; overflow:hidden; position:relative;
          `);
          y.title = d;
          const w = document.createElement("canvas");
          w.width = 32, w.height = 32, w.style.cssText = "width:36px; height:36px; image-rendering:pixelated;";
          const T = w.getContext("2d");
          T.imageSmoothingEnabled = !1, T.drawImage(g, 0, 0, g.naturalWidth, g.naturalHeight, 0, 0, 32, 32), y.appendChild(w);
          const b = this.el("div", "position:absolute; bottom:0; left:1px; right:1px; font-size:7px; color:#888; text-align:center; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;");
          b.textContent = d, y.appendChild(b), y.addEventListener("click", () => {
            this.selectedTileKey = d, this.buildTabContent();
          }), r.appendChild(y);
        }, g.src = m;
      }
    }).catch(() => {
    });
  }
  paintTile(e, t) {
    const i = this.tileSize, n = Math.floor(e / i), s = Math.floor(t / i);
    this.selectedTileKey === "" && this.props.occupiesTile(n, s) || this.mv.setTile(n, s, this.selectedTileKey);
  }
  // --- Props overlay ---
  renderPropsOverlay(e) {
    const t = this.tileSize;
    for (const i of this.props.pieces)
      for (const n of i.anchors)
        this.drawAnchorDot(e, (i.x + n.ox) * t + t / 2, (i.y + n.oy) * t + t / 2, n.type, 3);
    for (const i of this.props.selected) {
      e.strokeStyle = "#00ff88", e.lineWidth = 1.5, e.setLineDash([4, 4]), e.strokeRect(i.x * t, i.y * t, i.w * t, i.h * t), e.setLineDash([]), e.fillStyle = "rgba(0,0,0,0.7)", e.font = "7px monospace";
      const n = `${i.id} (${i.x.toFixed(1)}, ${i.y.toFixed(1)})`, s = e.measureText(n).width;
      e.fillRect(i.x * t, i.y * t - 10, s + 4, 10), e.fillStyle = "#00ff88", e.fillText(n, i.x * t + 2, i.y * t - 2);
    }
    this.refreshTabContent();
  }
  // --- Citizens overlay ---
  renderCitizensOverlay(e) {
    const t = this.tileSize;
    for (const i of this.mv.getCitizens()) {
      if (!i.visible) continue;
      const n = i.x + t / 2, s = i.y + t / 2, a = i.agentId === this.selectedCitizenId;
      e.beginPath(), e.arc(n, s, a ? 14 : 10, 0, Math.PI * 2), e.strokeStyle = a ? "#00ff88" : "rgba(255,255,255,0.4)", e.lineWidth = a ? 2 : 1, e.stroke(), a && (e.fillStyle = "rgba(0,255,136,0.1)", e.fill());
    }
    this.refreshTabContent();
  }
  // --- Behavior overlay ---
  renderBehaviorOverlay(e) {
    const t = this.tileSize;
    e.strokeStyle = "rgba(255,255,255,0.15)", e.lineWidth = 1;
    for (const i of this.props.pieces)
      e.strokeRect(i.x * t, i.y * t, i.w * t, i.h * t);
    for (const i of this.props.pieces)
      for (let n = 0; n < i.anchors.length; n++) {
        const s = i.anchors[n], a = (i.x + s.ox) * t + t / 2, r = (i.y + s.oy) * t + t / 2, o = i === this.selAnchorPiece && n === this.selAnchorIdx;
        this.drawAnchorDot(e, a, r, s.type, o ? 7 : 5), o && (e.strokeStyle = "#fff", e.lineWidth = 1.5, e.beginPath(), e.arc(a, r, 9, 0, Math.PI * 2), e.stroke()), e.globalAlpha = 0.6, e.fillStyle = "#ccc", e.font = "6px monospace", e.fillText(s.name, (i.x + s.ox) * t + 2, (i.y + s.oy) * t - 2), e.globalAlpha = 1;
      }
    for (const i of this.props.wanderPoints) {
      const n = i.x * t + t / 2, s = i.y * t + t / 2;
      this.drawAnchorDot(e, n, s, "wander", 5), e.strokeStyle = "#888", e.lineWidth = 1, e.setLineDash([2, 2]), e.beginPath(), e.arc(n, s, 7, 0, Math.PI * 2), e.stroke(), e.setLineDash([]), e.globalAlpha = 0.5, e.font = "6px monospace", e.fillStyle = "#888", e.fillText(i.name, i.x * t + 2, i.y * t - 2), e.globalAlpha = 1;
    }
    this.refreshTabContent();
  }
  drawAnchorDot(e, t, i, n, s) {
    e.fillStyle = M[n], e.globalAlpha = 0.85, e.beginPath(), e.arc(t, i, s, 0, Math.PI * 2), e.fill(), e.globalAlpha = 1;
  }
  // --- Panel ---
  buildPanel() {
    if (this.panel) return;
    const e = this.canvas.parentElement;
    this.wrapper = document.createElement("div"), this.wrapper.id = "editor-wrapper", this.wrapper.style.cssText = "display:flex; gap:0; align-items:flex-start;", e.parentElement.insertBefore(this.wrapper, e), this.wrapper.appendChild(e), this.panel = document.createElement("div"), this.panel.style.cssText = `
      width: 190px;
      background: #111;
      border: 2px solid #00ff88;
      border-left: none;
      border-radius: 0 4px 4px 0;
      font-family: 'Courier New', monospace;
      font-size: 11px;
      color: #ccc;
      display: flex;
      flex-direction: column;
      overflow: hidden;
    `;
    const t = () => {
      const c = this.canvas.clientHeight || this.canvas.height;
      this.panel && (this.panel.style.height = c + "px");
    };
    t(), new ResizeObserver(t).observe(this.canvas);
    const n = document.createElement("style");
    n.textContent = `
      #editor-wrapper ::-webkit-scrollbar { width: 6px; }
      #editor-wrapper ::-webkit-scrollbar-track { background: #1a1a2e; }
      #editor-wrapper ::-webkit-scrollbar-thumb { background: #444; border-radius: 3px; }
      #editor-wrapper ::-webkit-scrollbar-thumb:hover { background: #666; }
    `, document.head.appendChild(n);
    const s = document.createElement("div");
    s.style.cssText = "display:flex; border-bottom:1px solid #333;";
    const a = ["world", "props", "citizens", "behavior", "generate"];
    for (const c of a) {
      const l = document.createElement("div"), p = {
        world: "World",
        props: "Props",
        citizens: "Cit",
        behavior: "Behv",
        generate: "Gen"
      };
      l.textContent = p[c], l.style.cssText = `
        flex:1; text-align:center; padding:6px 0; cursor:pointer;
        font-size:9px; text-transform:uppercase; letter-spacing:0.5px;
        transition: background 0.1s, color 0.1s;
      `, l.addEventListener("click", () => this.switchTab(c)), s.appendChild(l), this.tabBtns.set(c, l);
    }
    this.panel.appendChild(s), this.tabContent = document.createElement("div"), this.tabContent.style.cssText = "flex:1; overflow-y:auto; display:flex; flex-direction:column;", this.panel.appendChild(this.tabContent);
    const r = document.createElement("div");
    r.style.cssText = "display:flex; border-top:1px solid #333; padding:4px 6px; gap:4px; margin-top:auto;";
    const o = this.makeBtn("⟵ Undo", () => this.undo()), h = this.makeBtn("Redo ⟶", () => this.redo());
    o.style.cssText += "flex:1; text-align:center; font-size:11px; padding:4px 0;", h.style.cssText += "flex:1; text-align:center; font-size:11px; padding:4px 0;", r.appendChild(o), r.appendChild(h), this.panel.appendChild(r), this.wrapper.appendChild(this.panel), this.updateTabStyles(), this.buildTabContent();
  }
  updateTabStyles() {
    for (const [e, t] of this.tabBtns)
      e === this.tab ? (t.style.background = "#00ff8825", t.style.color = "#00ff88", t.style.borderBottom = "2px solid #00ff88") : (t.style.background = "transparent", t.style.color = "#666", t.style.borderBottom = "2px solid transparent");
  }
  switchTab(e) {
    e !== this.tab && (this.tab = e, this.props.selected.clear(), this.selAnchorPiece = null, this.selAnchorIdx = -1, this.selectedCitizenId = null, this.updateTabStyles(), this.buildTabContent());
  }
  buildTabContent() {
    if (this.tabContent)
      switch (this.tabContent.innerHTML = "", this.tab) {
        case "world":
          this.buildWorldTab();
          break;
        case "props":
          this.buildPropsTab();
          break;
        case "citizens":
          this.buildCitizensTab();
          break;
        case "behavior":
          this.buildBehaviorTab();
          break;
        case "generate":
          this.buildGenerateTab();
          break;
      }
  }
  refreshTabContent() {
    switch (this.tab) {
      case "world":
        break;
      case "props":
        this.refreshPropsTab();
        break;
      case "citizens":
        this.refreshCitizensTab();
        break;
      case "behavior":
        this.refreshBehaviorTab();
        break;
    }
  }
  buildPropsTab() {
    const e = this.tabContent, t = this.el("div", "padding:6px 10px; border-bottom:1px solid #333; line-height:1.6;");
    t.innerHTML = [
      '<span style="color:#00ff88">Drag</span> move',
      '<span style="color:#00ff88">Shift+Click</span> multi-select',
      '<span style="color:#00ff88">⌘C / ⌘V</span> copy/paste',
      '<span style="color:#00ff88">Arrows</span> nudge',
      '<span style="color:#00ff88">+ / -</span> resize',
      '<span style="color:#00ff88">L</span> layer',
      '<span style="color:#00ff88">Del</span> remove',
      '<span style="color:#00ff88">S</span> save'
    ].join("<br>"), e.appendChild(t), this.propsInfo = this.el("div", "padding:6px 10px; border-bottom:1px solid #333; min-height:36px; color:#888;"), this.propsInfo.innerHTML = '<span style="color:#555">Click a piece</span>', e.appendChild(this.propsInfo);
    const i = this.el("div", "padding:4px 10px; color:#555; font-size:9px; text-transform:uppercase; letter-spacing:1px;");
    i.textContent = "Inventory", e.appendChild(i);
    const n = this.el("div", "padding:4px 8px; display:flex; flex-wrap:wrap; gap:4px;");
    for (const [s, a] of this.props.getImageSrcs()) {
      const r = this.el("div", `
        width:40px; height:40px; border:1px solid #333; border-radius:3px;
        display:flex; align-items:center; justify-content:center;
        cursor:pointer; background:#1a1a2e;
      `);
      r.title = s;
      const o = document.createElement("img");
      o.src = a, o.style.cssText = "max-width:34px; max-height:34px; image-rendering:pixelated;", r.appendChild(o), r.addEventListener("mouseenter", () => {
        r.style.borderColor = "#00ff88";
      }), r.addEventListener("mouseleave", () => {
        r.style.borderColor = "#333";
      }), r.addEventListener("click", () => {
        this.beginAction();
        const h = this.props.addPiece(s);
        h && (this.props.selected.clear(), this.props.selected.add(h)), this.commitAction();
      }), n.appendChild(r);
    }
    e.appendChild(n);
  }
  refreshPropsTab() {
    if (!this.propsInfo) return;
    const e = this.props.selected;
    if (e.size === 0) {
      this.propsInfo.innerHTML = '<span style="color:#555">Click a piece</span>';
      return;
    }
    if (e.size > 1) {
      this.propsInfo.innerHTML = `<span style="color:#00ff88">${e.size} pieces selected</span>`;
      return;
    }
    const t = [...e][0], i = t.anchors.length > 0 ? t.anchors.map((n) => `<span style="color:${M[n.type]}">●</span> ${n.name}`).join("<br>") : '<span style="color:#555">no anchors</span>';
    this.propsInfo.innerHTML = [
      `<span style="color:#00ff88">${t.id}</span>`,
      `pos: ${t.x.toFixed(2)}, ${t.y.toFixed(2)}`,
      `size: ${t.w.toFixed(1)}×${t.h.toFixed(1)}  layer: <span style="color:${t.layer === "above" ? "#ff8844" : "#4488ff"}">${t.layer}</span>`,
      i
    ].join("<br>");
  }
  buildCitizensTab() {
    const e = this.tabContent, t = this.el("div", "padding:6px 10px; border-bottom:1px solid #333;"), i = this.makeBtn("+ Add Citizen", () => this.showAddCitizenUI());
    i.style.cssText += "width:100%; text-align:center; padding:5px 0; border-color:#00ff88; color:#00ff88;", t.appendChild(i), e.appendChild(t), this.citizensList = this.el("div", "padding:4px 8px; border-bottom:1px solid #333;"), this.rebuildCitizensList(), e.appendChild(this.citizensList), this.citizensInfo = this.el("div", "padding:6px 10px; min-height:40px; color:#888;"), this.citizensInfo.innerHTML = '<span style="color:#555">Select a citizen</span>', e.appendChild(this.citizensInfo);
  }
  showAddCitizenUI() {
    if (!this.tabContent) return;
    this.tabContent.innerHTML = "";
    const e = this.tabContent, t = this.el("div", "padding:6px 10px; border-bottom:1px solid #333; color:#00ff88; font-size:11px;");
    t.textContent = "Add Citizen", e.appendChild(t);
    const i = this.el("div", "padding:8px 10px; display:flex; flex-direction:column; gap:6px;"), n = this.el("div", "color:#888; font-size:9px;");
    n.textContent = "Name", i.appendChild(n);
    const s = document.createElement("input");
    s.placeholder = "e.g. nova", s.style.cssText = "background:#222; border:1px solid #444; color:#ccc; padding:4px; font-family:inherit; font-size:10px; border-radius:2px;", i.appendChild(s);
    const a = this.el("div", "color:#888; font-size:9px;");
    a.textContent = "Sprite", i.appendChild(a);
    let r = "";
    const o = this.el("div", "display:flex; flex-wrap:wrap; gap:4px;"), h = /* @__PURE__ */ new Map(), c = (b) => {
      r = b;
      for (const [C, v] of h)
        v.style.borderColor = C === b ? "#00ff88" : "#333", v.style.background = C === b ? "#00ff8815" : "#1a1a2e";
    }, l = (b, C) => {
      if (h.has(b)) return;
      const v = this.el("div", `
        width:52px; height:64px; border:2px solid #333; border-radius:3px;
        cursor:pointer; background:#1a1a2e; overflow:hidden;
        display:flex; flex-direction:column; align-items:center; justify-content:center;
      `);
      v.title = b, h.set(b, v);
      const k = document.createElement("canvas");
      k.width = 48, k.height = 48, k.style.cssText = "image-rendering:pixelated;";
      const z = k.getContext("2d");
      z.imageSmoothingEnabled = !1, z.drawImage(C, 0, 0, 64, 64, 0, 0, 48, 48), v.appendChild(k);
      const I = this.el("div", "color:#888; font-size:7px; text-align:center; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; width:100%; padding:0 2px;");
      I.textContent = b, v.appendChild(I), v.addEventListener("click", () => c(b)), o.appendChild(v), h.size === 1 && c(b);
    };
    for (const b of this.mv.getCitizens()) {
      const C = this.citizenSprites.get(b.agentId) || b.name, v = b.spriteSheet.getImage("walk");
      v && l(C, v);
    }
    const p = this.el("div", "color:#555; font-size:9px;");
    p.textContent = "Loading sprites...", o.appendChild(p), fetch(`${this.apiBase}/api/citizens`).then((b) => b.json()).then((b) => {
      p.remove();
      for (const C of b) {
        if (h.has(C)) continue;
        const v = new Image();
        v.onload = () => l(C, v), v.src = `/universal_assets/citizens/${C}_walk.png`;
      }
    }).catch(() => {
      p.textContent = "";
    }), i.appendChild(o);
    const f = this.el("div", "color:#888; font-size:9px;");
    f.textContent = "Type", i.appendChild(f);
    const d = this.el("div", "display:flex; gap:4px;");
    let u = "npc";
    const m = this.el("div", "flex:1; text-align:center; padding:4px 0; cursor:pointer; font-size:9px; border-radius:2px; border:1px solid #00ff88; color:#00ff88; background:#00ff8815;");
    m.textContent = "NPC";
    const g = this.el("div", "flex:1; text-align:center; padding:4px 0; cursor:pointer; font-size:9px; border-radius:2px; border:1px solid #444; color:#888; background:transparent;");
    g.textContent = "Agent", m.addEventListener("click", () => {
      u = "npc", m.style.borderColor = "#00ff88", m.style.color = "#00ff88", m.style.background = "#00ff8815", g.style.borderColor = "#444", g.style.color = "#888", g.style.background = "transparent";
    }), g.addEventListener("click", () => {
      u = "agent", g.style.borderColor = "#00ff88", g.style.color = "#00ff88", g.style.background = "#00ff8815", m.style.borderColor = "#444", m.style.color = "#888", m.style.background = "transparent";
    }), d.appendChild(m), d.appendChild(g), i.appendChild(d);
    const y = this.el("div", "display:flex; gap:4px; margin-top:4px;"), w = this.makeBtn("Cancel", () => this.buildTabContent());
    w.style.cssText += "flex:1; text-align:center; padding:5px 0;";
    const T = this.makeBtn("Create", async () => {
      var L, P, R;
      const b = s.value.trim();
      if (!b) return;
      const C = b.toLowerCase().replace(/[^a-z0-9]+/g, "_");
      if (this.mv.getCitizen(C)) {
        s.style.borderColor = "#ef4444";
        return;
      }
      const v = r || C, k = this.props.getLocations().filter((te) => te.type === "work"), z = this.props.wanderPoints, I = ((L = k[this.mv.getCitizens().length]) == null ? void 0 : L.name) ?? ((P = z[0]) == null ? void 0 : P.name) ?? "wander_0";
      this.citizenTypes.set(C, u), this.citizenSprites.set(C, v);
      const { createStandardSpriteConfig: S } = await Promise.resolve().then(() => he), A = this.mv.getSpriteSheetConfig(v) ?? S(v);
      await this.mv.addCitizen(
        { agentId: C, name: b, sprite: v, position: I, npc: u === "npc" },
        A
      ), u === "npc" && ((R = this.mv.getCitizen(C)) == null || R.updateState("idle", null, 1)), this.props.save(), await this.saveScene(), this.buildTabContent();
    });
    T.style.cssText += "flex:1; text-align:center; padding:5px 0; border-color:#00ff88; color:#00ff88;", y.appendChild(w), y.appendChild(T), i.appendChild(y), e.appendChild(i);
  }
  rebuildCitizensList() {
    if (this.citizensList) {
      this.citizensList.innerHTML = "";
      for (const e of this.mv.getCitizens()) {
        const t = this.citizenTypes.get(e.agentId) ?? "agent", i = e.agentId === this.selectedCitizenId, n = this.el("div", `
        padding:4px 6px; cursor:pointer; display:flex; align-items:center; gap:6px;
        border-radius:3px; margin-bottom:2px;
        border:1px solid ${i ? "#00ff88" : "transparent"};
        background:${i ? "#00ff8815" : "transparent"};
      `), s = e.spriteSheet.getImage("walk");
        if (s) {
          const h = document.createElement("canvas");
          h.width = 24, h.height = 24, h.style.cssText = "image-rendering:pixelated; flex-shrink:0;";
          const c = h.getContext("2d");
          c.imageSmoothingEnabled = !1, c.drawImage(s, 0, 0, 64, 64, 0, 0, 24, 24), n.appendChild(h);
        } else {
          const h = this.el("span", `
          width:6px; height:6px; border-radius:50%; display:inline-block;
          background:${this.stateColor(e.state)};
        `);
          n.appendChild(h);
        }
        const a = this.el("span", "flex:1;");
        a.textContent = e.name, n.appendChild(a);
        const r = this.el("span", `font-size:8px; padding:1px 4px; border-radius:2px; border:1px solid ${t === "agent" ? "#818cf8" : "#fbbf24"}; color:${t === "agent" ? "#818cf8" : "#fbbf24"};`);
        r.textContent = t.toUpperCase(), n.appendChild(r);
        const o = this.el("span", "color:#ef4444; font-size:11px; cursor:pointer; padding:0 2px; opacity:0.5;");
        o.textContent = "×", o.title = "Remove character", o.addEventListener("click", (h) => {
          h.stopPropagation(), this.mv.removeCitizen(e.agentId), this.citizenTypes.delete(e.agentId), this.citizenSprites.delete(e.agentId), this.selectedCitizenId === e.agentId && (this.selectedCitizenId = null, this.citizensBuiltFor = null), this.props.save(), this.saveScene(), this.rebuildCitizensList();
        }), o.addEventListener("mouseenter", () => {
          o.style.opacity = "1";
        }), o.addEventListener("mouseleave", () => {
          o.style.opacity = "0.5";
        }), n.appendChild(o), n.addEventListener("click", () => {
          this.selectedCitizenId = e.agentId, this.citizensBuiltFor = null, this.rebuildCitizensList();
        }), this.citizensList.appendChild(n);
      }
    }
  }
  refreshCitizensTab() {
    if (!this.citizensInfo) return;
    if (!this.selectedCitizenId) {
      this.citizensBuiltFor = null, this.citizensInfo.innerHTML = '<span style="color:#555">Select a citizen</span>';
      return;
    }
    if (this.citizensBuiltFor === this.selectedCitizenId) return;
    this.citizensBuiltFor = this.selectedCitizenId;
    const e = this.mv.getCitizen(this.selectedCitizenId);
    if (!e) return;
    const t = /* @__PURE__ */ new Map();
    for (const f of this.mv.getCitizens())
      t.set(f.getHomePosition(), f.name);
    const i = this.props.getLocations().filter((f) => f.type === "work"), n = e.getHomePosition(), r = (i.some((f) => f.name === n) ? "" : `<option value="${n}" selected style="color:#f44">${n} (not a desk)</option>`) + i.map((f) => {
      const d = t.get(f.name), u = n === f.name, m = d && !u;
      return `<option value="${f.name}" ${u ? "selected" : ""} ${m ? "disabled" : ""}>${f.name}${m ? ` (${d})` : ""}</option>`;
    }).join(""), o = this.citizenTypes.get(e.agentId) ?? "agent", h = o === "agent" ? "#818cf8" : "#fbbf24", c = [
      `<span style="color:#00ff88">${e.name}</span> <span style="color:#555">(${e.agentId})</span>`,
      `type: <span style="color:${h}">${o}</span> <span id="ed-toggle-type" style="color:#555;cursor:pointer;font-size:9px;text-decoration:underline;">[toggle]</span>`,
      `state: ${e.state}`,
      `desk: <select id="ed-home-select" style="background:#222;border:1px solid #444;color:#ccc;font-family:inherit;font-size:10px;padding:1px 2px;border-radius:2px;">${r}</select>`
    ];
    o === "agent" ? c.push(
      `<div style="margin-top:6px;padding:4px 6px;background:#1a1a2e;border:1px solid #333;border-radius:3px;font-size:9px;"><div style="color:#818cf8;margin-bottom:2px;">Heartbeat endpoint:</div><code style="color:#ccc;word-break:break-all;">POST /api/heartbeat</code><br><code style="color:#888;word-break:break-all;">{"agent":"${e.agentId}","state":"working","task":"doing stuff","energy":0.8}</code></div>`
    ) : c.push('<div style="margin-top:4px;color:#555;font-size:9px;">NPC — auto-idle, no heartbeat needed</div>'), this.citizensInfo.innerHTML = c.join("<br>");
    const l = this.citizensInfo.querySelector("#ed-home-select");
    l == null || l.addEventListener("change", () => {
      this.beginAction(), e.setHomePosition(l.value), this.citizensBuiltFor = null, this.rebuildCitizensList(), this.commitAction();
    });
    const p = this.citizensInfo.querySelector("#ed-toggle-type");
    p == null || p.addEventListener("click", () => {
      const d = (this.citizenTypes.get(e.agentId) ?? "agent") === "agent" ? "npc" : "agent";
      this.citizenTypes.set(e.agentId, d), d === "npc" && e.updateState("idle", null, 1), this.citizensBuiltFor = null, this.rebuildCitizensList(), this.refreshCitizensTab(), this.saveScene();
    });
  }
  stateColor(e) {
    return {
      working: "#4ade80",
      idle: "#fbbf24",
      sleeping: "#818cf8",
      thinking: "#f472b6",
      error: "#ef4444",
      speaking: "#22d3ee"
    }[e] ?? "#555";
  }
  buildBehaviorTab() {
    const e = this.tabContent, t = this.el("div", "padding:6px 10px; border-bottom:1px solid #333; line-height:1.6;");
    t.innerHTML = [
      '<span style="color:#00ff88">Click</span> select anchor',
      '<span style="color:#00ff88">Drag</span> reposition',
      '<span style="color:#00ff88">T</span> cycle type',
      '<span style="color:#00ff88">Del</span> remove anchor',
      '<span style="color:#00ff88">S</span> save'
    ].join("<br>"), e.appendChild(t);
    const i = this.el("div", "padding:4px 10px; border-bottom:1px solid #333; font-size:10px; line-height:1.6;");
    i.innerHTML = Object.entries(M).map(
      ([s, a]) => `<span style="color:${a}">●</span> ${s}`
    ).join("&nbsp;&nbsp;"), e.appendChild(i), this.behaviorInfo = this.el("div", "padding:6px 10px; min-height:40px; color:#888;"), this.behaviorInfo.innerHTML = '<span style="color:#555">Click an anchor</span>', e.appendChild(this.behaviorInfo);
    const n = this.el("div", "padding:4px 8px; overflow-y:auto; flex:1;");
    for (const s of this.props.pieces) {
      if (s.anchors.length === 0) continue;
      const a = this.el("div", "margin-bottom:6px;"), r = this.el("div", "color:#555; font-size:9px; padding:2px 0;");
      r.textContent = `${s.id} (${s.x.toFixed(1)}, ${s.y.toFixed(1)})`, a.appendChild(r);
      for (let o = 0; o < s.anchors.length; o++) {
        const h = s.anchors[o], c = this.el("div", `
          padding:2px 4px; cursor:pointer; border-radius:2px;
          border:1px solid ${s === this.selAnchorPiece && o === this.selAnchorIdx ? "#fff" : "transparent"};
        `);
        c.innerHTML = `<span style="color:${M[h.type]}">●</span> ${h.name} <span style="color:#555">(${h.type})</span>`;
        const l = this.props.pieces.indexOf(s);
        c.addEventListener("click", () => {
          this.selAnchorPiece = this.props.pieces[l], this.selAnchorIdx = o, this.buildTabContent();
        }), a.appendChild(c);
      }
      n.appendChild(a);
    }
    e.appendChild(n);
  }
  refreshBehaviorTab() {
    if (!this.behaviorInfo) return;
    if (!this.selAnchorPiece || this.selAnchorIdx < 0) {
      this.behaviorInfo.innerHTML = '<span style="color:#555">Click an anchor</span>';
      return;
    }
    const e = this.selAnchorPiece.anchors[this.selAnchorIdx];
    e && (this.behaviorInfo.innerHTML = [
      `<span style="color:${M[e.type]}">●</span> <span style="color:#fff">${e.name}</span>`,
      `type: <span style="color:${M[e.type]}">${e.type}</span>`,
      `offset: ${e.ox.toFixed(2)}, ${e.oy.toFixed(2)}`,
      `world: ${(this.selAnchorPiece.x + e.ox).toFixed(1)}, ${(this.selAnchorPiece.y + e.oy).toFixed(1)}`
    ].join("<br>"));
  }
  // --- Generate tab ---
  buildGenerateTab() {
    const e = this.tabContent, t = localStorage.getItem("miniverse_fal_key") ?? "", i = this.el("div", "padding:6px 10px; border-bottom:1px solid #333;");
    if (t) {
      const g = this.el("div", "display:flex; align-items:center; gap:6px;"), y = this.el("span", "color:#00ff88; font-size:10px;");
      y.textContent = "✓ API key set", g.appendChild(y);
      const w = this.makeBtn("Clear", () => {
        localStorage.removeItem("miniverse_fal_key"), this.buildTabContent();
      });
      g.appendChild(w), i.appendChild(g);
    } else {
      const g = this.el("div", "color:#888; font-size:9px; margin-bottom:4px;");
      g.textContent = "FAL API Key", i.appendChild(g);
      const y = document.createElement("input");
      y.type = "password", y.placeholder = "Enter fal.ai key...", y.style.cssText = "width:100%; background:#222; border:1px solid #444; color:#ccc; padding:4px; font-family:inherit; font-size:10px; border-radius:2px; box-sizing:border-box;", i.appendChild(y);
      const w = this.makeBtn("Save Key", () => {
        y.value.trim() && (localStorage.setItem("miniverse_fal_key", y.value.trim()), this.buildTabContent());
      });
      w.style.marginTop = "4px", i.appendChild(w);
    }
    e.appendChild(i);
    const n = this.el("div", "padding:6px 10px; border-bottom:1px solid #333;"), s = this.el("div", "color:#555; font-size:9px; text-transform:uppercase; letter-spacing:1px; margin-bottom:4px;");
    s.textContent = "Type", n.appendChild(s);
    const a = ["props", "texture", "character"], r = this.el("div", "display:flex; gap:4px;");
    for (const g of a) {
      const y = this.el("div", `
        flex:1; text-align:center; padding:4px 0; cursor:pointer;
        font-size:9px; border-radius:2px;
        border:1px solid ${g === this.genType ? "#00ff88" : "#444"};
        color:${g === this.genType ? "#00ff88" : "#888"};
        background:${g === this.genType ? "#00ff8815" : "transparent"};
      `);
      y.textContent = g.charAt(0).toUpperCase() + g.slice(1), y.addEventListener("click", () => {
        this.genType = g, this.buildTabContent();
      }), r.appendChild(y);
    }
    n.appendChild(r), e.appendChild(n);
    const o = this.el("div", "padding:6px 10px; border-bottom:1px solid #333;"), h = this.el("div", "color:#555; font-size:9px; text-transform:uppercase; letter-spacing:1px; margin-bottom:4px;");
    h.textContent = "Prompt", o.appendChild(h);
    const c = document.createElement("textarea");
    c.placeholder = this.genType === "props" ? "A wooden desk with monitor..." : this.genType === "texture" ? "Light oak wood planks..." : "Young developer, blue hoodie...", c.style.cssText = "width:100%; height:60px; background:#222; border:1px solid #444; color:#ccc; padding:4px; font-family:inherit; font-size:10px; border-radius:2px; resize:vertical; box-sizing:border-box;", o.appendChild(c);
    const l = this.el("div", "margin-top:4px; display:flex; align-items:center; gap:6px;"), p = this.el("label", "color:#555; font-size:9px; cursor:pointer;");
    p.textContent = "+ Reference image";
    const f = document.createElement("input");
    f.type = "file", f.accept = "image/*", f.style.display = "none";
    let d = null;
    const u = this.el("span", "color:#888; font-size:9px;");
    f.addEventListener("change", () => {
      var w;
      const g = (w = f.files) == null ? void 0 : w[0];
      if (!g) return;
      const y = new FileReader();
      y.onload = () => {
        d = y.result.split(",")[1], u.textContent = g.name, u.style.color = "#00ff88";
      }, y.readAsDataURL(g);
    }), p.addEventListener("click", () => f.click()), l.appendChild(p), l.appendChild(f), l.appendChild(u), o.appendChild(l);
    const m = this.makeBtn(this.genBusy ? "Generating..." : "Generate", async () => {
      if (this.genBusy) return;
      const g = c.value.trim();
      if (!g) return;
      const y = localStorage.getItem("miniverse_fal_key");
      if (!y) {
        this.genStatus = "Set your FAL API key first", this.buildTabContent();
        return;
      }
      this.genBusy = !0, this.genStatus = "Generating...", this.genPreview = null, this.buildTabContent(), (async () => {
        var T;
        try {
          const b = {
            type: this.genType,
            prompt: g,
            falKey: y,
            worldId: this.worldId
          };
          d && (b.image = d);
          const C = `${this.apiBase}/api/generate`;
          console.log("[gen] Sending request...", C, b.type);
          let v;
          try {
            v = await fetch(C, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(b)
            });
          } catch (z) {
            console.error("[gen] Fetch failed:", z), this.genStatus = "Cannot reach /api/generate. Make sure your dev server has the generate plugin loaded.", this.genBusy = !1, this.buildTabContent();
            return;
          }
          console.log("[gen] Response status:", v.status);
          const k = await v.json();
          if (console.log("[gen] Result:", k), k.ok) {
            this.genPreview = k.path;
            const z = k.id || ((T = k.path.split("/").pop()) == null ? void 0 : T.replace(".png", "")) || `gen_${Date.now()}`;
            if (console.log("[gen] Loading sprite:", z, k.path), this.genType === "props" && k.path) {
              let I = !1;
              for (let A = 0; A < 3; A++)
                try {
                  await this.props.loadSprite(z, k.path + "?t=" + Date.now()), I = !0;
                  break;
                } catch {
                  console.log(`[gen] Sprite load attempt ${A + 1} failed, retrying...`), await new Promise((L) => setTimeout(L, 1e3));
                }
              if (!I) {
                this.genStatus = "Sprite generated but failed to load image. Try refreshing.", this.genBusy = !1, this.buildTabContent();
                return;
              }
              console.log("[gen] Sprite loaded, adding piece..."), this.beginAction();
              const S = this.props.addPiece(z);
              console.log("[gen] Piece added:", S == null ? void 0 : S.id, S == null ? void 0 : S.x, S == null ? void 0 : S.y), S && (this.props.selected.clear(), this.props.selected.add(S)), this.commitAction(), this.genStatus = `Added "${z}" to scene`, this.props.save(), await this.saveScene(), console.log("[gen] Scene saved");
            } else if (this.genType === "texture" && k.path) {
              let I = null;
              for (let S = 0; S < 3; S++)
                try {
                  I = await new Promise((A, L) => {
                    const P = new Image();
                    P.onload = () => A(P), P.onerror = () => L(new Error("Failed to load texture")), P.src = k.path + "?t=" + Date.now();
                  });
                  break;
                } catch {
                  console.log(`[gen] Texture load attempt ${S + 1} failed, retrying...`), await new Promise((A) => setTimeout(A, 1e3));
                }
              if (!I) {
                this.genStatus = "Texture generated but failed to load. Try refreshing.", this.genBusy = !1, this.buildTabContent();
                return;
              }
              this.mv.addTile(z, I, k.path), this.selectedTileKey = z, this.genStatus = `Added tile "${z}"`, await this.saveScene();
            } else
              this.genStatus = `Saved to ${k.path}`;
          } else
            this.genStatus = k.error || "Generation failed";
        } catch (b) {
          console.error("[gen] Error:", b), this.genStatus = `Error: ${b}`;
        }
        this.genBusy = !1, console.log("[gen] Done, rebuilding tab. Status:", this.genStatus), this.buildTabContent();
      })();
    });
    if (m.style.cssText += "margin-top:6px; text-align:center; padding:6px 0; width:100%;", this.genBusy && (m.style.opacity = "0.5", m.style.cursor = "default"), o.appendChild(m), e.appendChild(o), this.genStatus) {
      const g = this.genStatus.startsWith("Error") || this.genStatus.startsWith("Set ") || this.genStatus.includes("failed"), y = this.genStatus === "Generating...", w = this.el("div", `padding:6px 10px; font-size:10px; color:${g ? "#ef4444" : y ? "#fbbf24" : "#00ff88"};`);
      w.textContent = this.genStatus, e.appendChild(w);
    }
    if (this.genPreview) {
      const g = this.el("div", "padding:6px 10px;"), y = document.createElement("img");
      y.src = this.genPreview, y.style.cssText = "max-width:100%; image-rendering:pixelated; border:1px solid #333; border-radius:3px;", g.appendChild(y), e.appendChild(g);
    }
  }
  // --- Input ---
  toWorld(e) {
    const t = this.canvas.getBoundingClientRect();
    return {
      x: (e.clientX - t.left) / this.scale,
      y: (e.clientY - t.top) / this.scale
    };
  }
  onMouseDown(e) {
    const { x: t, y: i } = this.toWorld(e);
    this.beginAction(), this.tab === "world" ? (this.paintTile(t, i), this.painting = !0, e.preventDefault()) : this.tab === "props" ? this.props.handleMouseDown(t, i, e.shiftKey) && e.preventDefault() : this.tab === "citizens" ? this.pickCitizen(t, i) : this.tab === "behavior" && (this.pickAnchor(t, i), e.preventDefault());
  }
  onMouseMove(e) {
    const { x: t, y: i } = this.toWorld(e);
    if (this.tab === "world" && this.painting)
      this.paintTile(t, i), e.preventDefault();
    else if (this.tab === "props")
      this.props.handleMouseMove(t, i), e.preventDefault();
    else if (this.tab === "behavior" && this.draggingAnchor && this.selAnchorPiece) {
      const n = this.tileSize, s = this.selAnchorPiece.anchors[this.selAnchorIdx];
      s && (s.ox = Math.round((t / n - this.selAnchorPiece.x) * 4) / 4, s.oy = Math.round((i / n - this.selAnchorPiece.y) * 4) / 4), e.preventDefault();
    }
  }
  onMouseUp(e) {
    this.tab === "props" && this.props.handleMouseUp(), this.painting = !1, this.draggingAnchor = !1, this.commitAction();
  }
  pickCitizen(e, t) {
    const i = this.tileSize;
    for (const n of this.mv.getCitizens()) {
      if (!n.visible) continue;
      const s = e - (n.x + i / 2), a = t - (n.y + i / 2);
      if (s * s + a * a < i * i) {
        this.selectedCitizenId = n.agentId, this.citizensBuiltFor = null, this.rebuildCitizensList();
        return;
      }
    }
    this.selectedCitizenId = null, this.citizensBuiltFor = null, this.rebuildCitizensList();
  }
  pickAnchor(e, t) {
    const i = this.tileSize, n = 8;
    for (const s of this.props.pieces)
      for (let a = 0; a < s.anchors.length; a++) {
        const r = s.anchors[a], o = (s.x + r.ox) * i + i / 2, h = (s.y + r.oy) * i + i / 2, c = e - o, l = t - h;
        if (c * c + l * l < n * n) {
          this.selAnchorPiece = s, this.selAnchorIdx = a, this.draggingAnchor = !0;
          return;
        }
      }
    this.selAnchorPiece = null, this.selAnchorIdx = -1;
  }
  onKeyDown(e) {
    if (!(e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement || e.target instanceof HTMLSelectElement)) {
      if (e.key === "e" || e.key === "E") {
        this.active = !this.active, this.active ? (this.buildPanel(), this.panel.style.display = "flex", this.canvas.addEventListener("mousedown", this.onMouseDown), this.canvas.addEventListener("mousemove", this.onMouseMove), this.canvas.addEventListener("mouseup", this.onMouseUp)) : (this.props.save(), this.saveScene(), this.panel && (this.panel.style.display = "none"), this.canvas.removeEventListener("mousedown", this.onMouseDown), this.canvas.removeEventListener("mousemove", this.onMouseMove), this.canvas.removeEventListener("mouseup", this.onMouseUp), this.props.selected.clear(), this.selAnchorPiece = null, this.selectedCitizenId = null);
        return;
      }
      if (this.active) {
        if ((e.ctrlKey || e.metaKey) && e.key === "z" && !e.shiftKey) {
          e.preventDefault(), this.undo();
          return;
        }
        if ((e.ctrlKey || e.metaKey) && (e.key === "z" && e.shiftKey || e.key === "y")) {
          e.preventDefault(), this.redo();
          return;
        }
        if ((e.key === "s" || e.key === "S") && !e.ctrlKey && !e.metaKey) {
          this.props.save(), this.saveScene();
          return;
        }
        this.tab === "props" ? (this.beginAction(), this.props.handleKey(e), this.commitAction()) : this.tab === "behavior" && (this.beginAction(), this.handleBehaviorKey(e), this.commitAction());
      }
    }
  }
  handleBehaviorKey(e) {
    if (!this.selAnchorPiece || this.selAnchorIdx < 0) return;
    const t = this.selAnchorPiece.anchors[this.selAnchorIdx];
    if (t) {
      if (e.key === "t" || e.key === "T") {
        const i = $.indexOf(t.type);
        t.type = $[(i + 1) % $.length], this.buildTabContent();
      }
      if ((e.key === "Delete" || e.key === "Backspace") && (this.selAnchorPiece.anchors.splice(this.selAnchorIdx, 1), this.selAnchorPiece = null, this.selAnchorIdx = -1, this.buildTabContent()), e.key.startsWith("Arrow")) {
        const i = e.shiftKey ? 1 : 0.25;
        e.key === "ArrowLeft" && (t.ox -= i), e.key === "ArrowRight" && (t.ox += i), e.key === "ArrowUp" && (t.oy -= i), e.key === "ArrowDown" && (t.oy += i), e.preventDefault();
      }
    }
  }
  // --- Undo / Redo ---
  captureState() {
    const e = {};
    for (const n of this.mv.getCitizens())
      e[n.agentId] = n.getHomePosition();
    const { cols: t, rows: i } = this.mv.getGridSize();
    return JSON.stringify({
      gridCols: t,
      gridRows: i,
      floor: this.mv.getFloorLayer(),
      props: this.props.getLayout(),
      characters: e,
      wanderPoints: this.props.wanderPoints
    });
  }
  restoreState(e) {
    const t = JSON.parse(e), { cols: i, rows: n } = this.mv.getGridSize();
    if ((t.gridCols !== i || t.gridRows !== n) && this.mv.resizeGrid(t.gridCols, t.gridRows), t.floor) {
      const s = this.mv.getFloorLayer();
      for (let a = 0; a < t.floor.length && a < s.length; a++)
        for (let r = 0; r < t.floor[a].length && r < s[a].length; r++)
          s[a][r] = t.floor[a][r];
    }
    if (this.props.setLayout(t.props ?? []), t.wanderPoints && this.props.setWanderPoints(t.wanderPoints), t.characters)
      for (const s of this.mv.getCitizens())
        t.characters[s.agentId] && s.setHomePosition(t.characters[s.agentId]);
    if (this.props.selected.clear(), this.selAnchorPiece = null, this.selAnchorIdx = -1, this.citizensBuiltFor = null, this.gridLabel) {
      const s = this.mv.getGridSize();
      this.gridLabel.textContent = `Grid: ${s.cols}×${s.rows}`;
    }
    this.buildTabContent();
  }
  beginAction() {
    this.preActionSnapshot = this.captureState();
  }
  commitAction() {
    if (!this.preActionSnapshot) return;
    if (this.captureState() === this.preActionSnapshot) {
      this.preActionSnapshot = null;
      return;
    }
    this.undoStack.push(this.preActionSnapshot), this.undoStack.length > this.maxHistory && this.undoStack.shift(), this.redoStack.length = 0, this.preActionSnapshot = null;
  }
  undo() {
    this.undoStack.length !== 0 && (this.redoStack.push(this.captureState()), this.restoreState(this.undoStack.pop()), console.log(`[editor] Undo (${this.undoStack.length} left)`));
  }
  redo() {
    this.redoStack.length !== 0 && (this.undoStack.push(this.captureState()), this.restoreState(this.redoStack.pop()), console.log(`[editor] Redo (${this.redoStack.length} left)`));
  }
  // --- Scene persistence ---
  buildSceneSnapshot() {
    const { cols: e, rows: t } = this.mv.getGridSize(), i = this.worldId ? `/worlds/${this.worldId}` : "", n = (d) => {
      let u = d.split("?")[0];
      return i && u.startsWith(i) && (u = u.slice(i.length)), u.startsWith("/") && (u = u.slice(1)), u;
    }, s = {};
    for (const [d, u] of this.props.getImageSrcs())
      s[d] = n(u);
    const a = this.mv.getCitizens().map((d) => ({
      agentId: d.agentId,
      name: d.name,
      sprite: this.citizenSprites.get(d.agentId) || d.name,
      position: d.getHomePosition(),
      type: this.citizenTypes.get(d.agentId) ?? "agent"
    })), r = {};
    for (const [d, u] of Object.entries(this.mv.getTiles()))
      r[d] = n(u);
    const o = this.props.getLayout().filter(
      (d) => d.x >= 0 && d.y >= 0 && d.x + d.w <= e && d.y + d.h <= t
    ), h = this.props.wanderPoints.filter(
      (d) => d.x >= 0 && d.y >= 0 && d.x < e && d.y < t
    ), c = /* @__PURE__ */ new Set();
    for (const d of o)
      for (const u of d.anchors ?? []) {
        const m = Math.round(d.x + u.ox), g = Math.round(d.y + u.oy);
        m >= 0 && g >= 0 && m < e && g < t && c.add(u.name);
      }
    for (const d of h)
      c.add(d.name);
    const l = a.filter((d) => c.has(d.position)), p = {
      props: this.props.getLayout().length - o.length,
      wanderPoints: this.props.wanderPoints.length - h.length,
      citizens: a.length - l.length
    };
    return p.props + p.wanderPoints + p.citizens > 0 && console.log(`[editor] Sanitized: removed ${p.props} props, ${p.wanderPoints} wander points, ${p.citizens} citizens outside ${e}x${t} grid`), {
      worldId: this.worldId || void 0,
      gridCols: e,
      gridRows: t,
      floor: this.mv.getFloorLayer(),
      tiles: r,
      props: o,
      wanderPoints: h,
      propImages: s,
      citizens: l
    };
  }
  async saveScene() {
    const e = this.buildSceneSnapshot();
    if (this.saveFn)
      try {
        await this.saveFn(e), console.log("[editor] Scene saved");
      } catch (t) {
        console.error("[editor] Save failed:", t);
      }
    else
      console.warn("[editor] No save function configured");
  }
  loadCitizenDefs(e) {
    if (e)
      for (const t of e) {
        this.citizenTypes.set(t.agentId, t.type), this.citizenSprites.set(t.agentId, t.sprite);
        const i = this.mv.getCitizen(t.agentId);
        i && (i.setHomePosition(t.position), t.type === "npc" && i.updateState("idle", null, 1));
      }
  }
  resizeGrid(e, t) {
    const { cols: i, rows: n } = this.mv.getGridSize();
    if (this.mv.resizeGrid(i + e, n + t), this.gridLabel) {
      const s = this.mv.getGridSize();
      this.gridLabel.textContent = `Grid: ${s.cols}×${s.rows}`;
    }
  }
  makeBtn(e, t) {
    const i = this.el("div", `
      padding:2px 5px; border:1px solid #444; border-radius:2px;
      cursor:pointer; font-size:9px; color:#ccc; background:#222;
    `);
    return i.textContent = e, i.addEventListener("mouseenter", () => {
      i.style.borderColor = "#00ff88";
    }), i.addEventListener("mouseleave", () => {
      i.style.borderColor = "#444";
    }), i.addEventListener("click", t), i;
  }
  // --- Helpers ---
  el(e, t) {
    const i = document.createElement(e);
    return i.style.cssText = t, i;
  }
  destroy() {
    if (window.removeEventListener("keydown", this.onKeyDown), this.canvas.removeEventListener("mousedown", this.onMouseDown), this.canvas.removeEventListener("mousemove", this.onMouseMove), this.canvas.removeEventListener("mouseup", this.onMouseUp), this.wrapper) {
      const e = this.canvas.parentElement;
      this.wrapper.parentElement.insertBefore(e, this.wrapper), this.wrapper.remove();
    }
  }
}
const E = class E {
  constructor(e) {
    this.citizens = [], this.objects = [], this.eventHandlers = /* @__PURE__ */ new Map(), this.particleTimers = /* @__PURE__ */ new Map(), this.typedLocations = [], this.reservation = new q(), this.spawningAgents = /* @__PURE__ */ new Set(), this.autoSpawnIndex = 0, this.lastTransitionTime = /* @__PURE__ */ new Map(), this.config = e;
    const t = e.scale ?? 2, i = e.width ?? 512, n = e.height ?? 384;
    this.renderer = new G(e.container, i, n, t), this.scene = new U(e.sceneConfig ?? re()), this.citizenLayer = new J(), this.particles = new V(), this.speechBubbles = new Q(), this.signal = new ee(e.signal), this.renderer.addLayer(this.scene), this.renderer.addLayer({
      order: 5,
      render: (s, a) => {
        for (const r of this.objects)
          r.update(a), r.draw(s);
      }
    });
    for (const s of this.citizenLayer.getLayers())
      this.renderer.addLayer(s);
    if (this.renderer.addLayer(this.particles), this.renderer.addLayer(this.speechBubbles), this.renderer.addLayer({
      order: 30,
      render: (s) => {
        for (const a of this.citizens) {
          if (!a.visible) continue;
          s.save(), s.font = "8px monospace", s.fillStyle = "rgba(0,0,0,0.6)";
          const r = s.measureText(a.name).width, o = a.x + (this.scene.config.tileWidth - r) / 2, h = a.y - a.spriteSheet.config.frameHeight + this.scene.config.tileHeight - 4 - a.getSittingOffset();
          s.fillRect(o - 2, h - 8, r + 4, 12), s.fillStyle = "#ffffff", s.fillText(a.name, o, h), s.restore();
        }
      }
    }), this.signal.onUpdate((s) => this.handleSignalUpdate(s)), this.signal.onEvent((s) => {
      var a, r;
      if (((a = s.action) == null ? void 0 : a.type) === "message" && ((r = s.action) != null && r.to)) {
        const o = this.citizens.find((c) => c.agentId === s.agentId), h = this.citizens.find((c) => c.agentId === s.action.to);
        if (o && h && o !== h) {
          const c = o.getTilePosition(), l = h.getTilePosition(), p = [[-1, 0], [1, 0], [0, -1], [0, 1]];
          let f = [];
          for (const [d, u] of p) {
            const m = this.scene.pathfinder.findPath(c.x, c.y, l.x + d, l.y + u);
            m.length > 1 && (f.length === 0 || m.length < f.length) && (f = m);
          }
          f.length > 1 && o.walkTo(f);
        }
      }
    }), this.renderer.canvas.addEventListener("click", (s) => this.handleClick(s)), e.objects)
      for (const s of e.objects)
        this.objects.push(new Z(s));
    this.renderer.addLayer({
      order: -1,
      render: (s, a) => {
        const r = {};
        for (const [o, h] of Object.entries(this.scene.config.locations))
          r[o] = { x: h.x, y: h.y };
        for (const o of this.citizens) {
          const h = this.getOtherHomeAnchors(o.agentId);
          o.update(a, this.scene.pathfinder, r, this.typedLocations, this.reservation, h), o.applySeparation(this.citizens, a), this.updateCitizenEffects(o, a);
        }
      }
    });
  }
  async start() {
    var t;
    const e = this.config.worldBasePath ?? `worlds/${this.config.world}`;
    await this.scene.load(e);
    for (const i of this.config.citizens) {
      const n = ((t = this.config.spriteSheets) == null ? void 0 : t[i.sprite]) ?? O(i.sprite), s = new _(n);
      await s.load(e);
      const a = new D(
        i,
        s,
        this.scene.config.tileWidth,
        this.scene.config.tileHeight
      ), r = this.scene.getLocation(i.position);
      if (r)
        a.setTilePosition(r.x, r.y);
      else {
        const o = this.typedLocations.find((h) => h.name === i.position);
        o && a.setTilePosition(o.x, o.y);
      }
      this.citizens.push(a);
    }
    this.citizenLayer.setCitizens(this.citizens), this.unstickCitizens(), this.signal.start(), this.renderer.start();
  }
  /** Nudge any citizen that can't pathfind to any destination to the nearest open tile */
  unstickCitizens() {
    var r;
    const e = this.scene.config.walkable, t = e.length, i = ((r = e[0]) == null ? void 0 : r.length) ?? 0, n = [[0, -1], [0, 1], [-1, 0], [1, 0]], s = this.typedLocations.filter((o) => o.type === "wander" || o.type === "social" || o.type === "utility").map((o) => ({ x: o.x, y: o.y })), a = (o, h) => {
      let c = 0;
      for (const [l, p] of n) {
        const f = o + l, d = h + p;
        f >= 0 && f < i && d >= 0 && d < t && e[d][f] && c++;
      }
      return c;
    };
    for (const o of this.citizens) {
      const h = o.getTilePosition();
      if (s.some(
        (d) => this.scene.pathfinder.findPath(h.x, h.y, d.x, d.y).length > 1
      )) continue;
      const l = /* @__PURE__ */ new Set(), p = [{ x: h.x, y: h.y }];
      l.add(`${h.x},${h.y}`);
      let f = !1;
      for (; p.length > 0; ) {
        const d = p.shift();
        for (const [u, m] of n) {
          const g = d.x + u, y = d.y + m, w = `${g},${y}`;
          if (!(g < 0 || g >= i || y < 0 || y >= t) && !l.has(w)) {
            if (l.add(w), e[y][g] && a(g, y) >= 2 && s.some(
              (b) => this.scene.pathfinder.findPath(g, y, b.x, b.y).length > 1
            )) {
              o.setTilePosition(g, y), console.log(`[miniverse] Unstuck "${o.agentId}" from (${h.x},${h.y}) to (${g},${y})`), f = !0;
              break;
            }
            p.push({ x: g, y });
          }
        }
        if (f) break;
      }
    }
  }
  stop() {
    this.renderer.stop(), this.signal.stop();
  }
  getCanvas() {
    return this.renderer.canvas;
  }
  addLayer(e) {
    this.renderer.addLayer(e);
  }
  on(e, t) {
    this.eventHandlers.has(e) || this.eventHandlers.set(e, /* @__PURE__ */ new Set()), this.eventHandlers.get(e).add(t);
  }
  off(e, t) {
    var i;
    (i = this.eventHandlers.get(e)) == null || i.delete(t);
  }
  emit(e, t) {
    const i = this.eventHandlers.get(e);
    if (i)
      for (const n of i)
        n(t);
  }
  triggerEvent(e, t) {
    if (e === "intercom") {
      for (const i of this.objects)
        i.config.type === "intercom" && i.activate();
      for (const i of this.citizens)
        i.visible && i.faceDirection("down");
      t != null && t.message && this.speechBubbles.show(
        this.renderer.canvas.width / (2 * (this.config.scale ?? 2)),
        20,
        String(t.message),
        4
      ), this.emit("intercom", t ?? {});
    }
  }
  setTypedLocations(e) {
    this.typedLocations = e;
  }
  /** Resize the grid by expanding right/down. Existing coords stay the same. */
  resizeGrid(e, t) {
    var h;
    const i = this.scene.config, n = i.walkable.length, s = ((h = i.walkable[0]) == null ? void 0 : h.length) ?? 0;
    if (e < 4 || t < 4) return;
    for (let c = 0; c < t; c++) {
      for (c >= n && (i.walkable[c] = new Array(e).fill(!0)); i.walkable[c].length < e; )
        i.walkable[c].push(!0);
      i.walkable[c].length = e;
    }
    i.walkable.length = t;
    const a = Object.keys(i.tiles)[0] ?? "floor";
    for (const c of i.layers) {
      for (let l = 0; l < t; l++) {
        for (l >= c.length && (c[l] = new Array(e).fill(a)); c[l].length < e; )
          c[l].push(a);
        c[l].length = e;
      }
      c.length = t;
      for (let l = 0; l < t; l++)
        for (let p = 0; p < e; p++)
          (l >= n || p >= s) && (c[l][p] = a);
    }
    const r = i.tileWidth, o = i.tileHeight;
    this.renderer.resize(e * r, t * o);
  }
  getGridSize() {
    var t;
    const e = this.scene.config.walkable;
    return { cols: ((t = e[0]) == null ? void 0 : t.length) ?? 0, rows: e.length };
  }
  getFloorLayer() {
    return this.scene.config.layers[0];
  }
  setTile(e, t, i) {
    const n = this.scene.config.layers[0];
    if (t >= 0 && t < n.length && e >= 0 && e < n[0].length) {
      n[t][e] = i;
      const s = this.scene.config.walkable;
      t < s.length && e < s[0].length && (s[t][e] = i !== "");
    }
  }
  getTiles() {
    return this.scene.config.tiles;
  }
  getTileImages() {
    return this.scene.getTileImages();
  }
  addTile(e, t, i) {
    this.scene.addTile(e, t), i && (this.scene.config.tiles[e] = i);
  }
  /** Update walkability grid: reset to base then overlay blocked tiles */
  updateWalkability(e) {
    var r, o, h, c;
    const t = this.scene.config.walkable, i = t.length, n = ((r = t[0]) == null ? void 0 : r.length) ?? 0, s = this.scene.config.layers[0];
    for (let l = 0; l < i; l++)
      for (let p = 0; p < n; p++) {
        const f = l === 0 || l === i - 1 || p === 0 || p === n - 1, d = ((o = s == null ? void 0 : s[l]) == null ? void 0 : o[p]) === "";
        t[l][p] = !f && !d;
      }
    for (const l of e) {
      const [p, f] = l.split(",").map(Number);
      f >= 0 && f < i && p >= 0 && p < n && (t[f][p] = !1);
    }
    const a = [[0, 1], [0, -1], [1, 0], [-1, 0]];
    for (const l of this.typedLocations) {
      l.y >= 0 && l.y < i && l.x >= 0 && l.x < n && ((h = s == null ? void 0 : s[l.y]) == null ? void 0 : h[l.x]) !== "" && (t[l.y][l.x] = !0);
      let p = !1;
      for (const [f, d] of a) {
        const u = l.x + f, m = l.y + d;
        if (u > 0 && u < n - 1 && m > 0 && m < i - 1 && t[m][u]) {
          p = !0;
          break;
        }
      }
      if (!p)
        for (const [f, d] of [[0, 1], [1, 0], [-1, 0], [0, -1]]) {
          const u = l.x + f, m = l.y + d;
          if (u > 0 && u < n - 1 && m > 0 && m < i - 1 && ((c = s == null ? void 0 : s[m]) == null ? void 0 : c[u]) !== "") {
            t[m][u] = !0;
            break;
          }
        }
    }
  }
  getReservation() {
    return this.reservation;
  }
  getCitizen(e) {
    return this.citizens.find((t) => t.agentId === e);
  }
  getCitizens() {
    return [...this.citizens];
  }
  getSpriteSheetKeys() {
    return Object.keys(this.config.spriteSheets ?? {});
  }
  getSpriteSheetConfig(e) {
    var t;
    return (t = this.config.spriteSheets) == null ? void 0 : t[e];
  }
  getBasePath() {
    return this.config.worldBasePath ?? `worlds/${this.config.world}`;
  }
  async addCitizen(e, t) {
    const i = t ?? O(e.sprite), n = new _(i), s = this.config.worldBasePath ?? `worlds/${this.config.world}`;
    await n.load(s);
    const a = new D(
      e,
      n,
      this.scene.config.tileWidth,
      this.scene.config.tileHeight
    ), r = this.scene.getLocation(e.position);
    if (r)
      a.setTilePosition(r.x, r.y);
    else {
      const o = this.typedLocations.find((h) => h.name === e.position);
      o && a.setTilePosition(o.x, o.y);
    }
    return this.citizens.push(a), this.citizenLayer.setCitizens(this.citizens), this.unstickCitizens(), a;
  }
  removeCitizen(e) {
    const t = this.citizens.findIndex((i) => i.agentId === e);
    t < 0 || (this.reservation.release(e), this.citizens.splice(t, 1), this.citizenLayer.setCitizens(this.citizens));
  }
  handleSignalUpdate(e) {
    for (const t of e) {
      const i = this.citizens.find((s) => s.agentId === t.id);
      if (!i) {
        this.config.autoSpawn !== !1 && t.state !== "offline" && !this.spawningAgents.has(t.id) && this.autoSpawnCitizen(t);
        continue;
      }
      if (i.isNpc) continue;
      const n = i.state;
      if (i.updateState(t.state, t.task, t.energy), n !== t.state) {
        const s = Date.now(), a = this.lastTransitionTime.get(i.agentId) ?? 0;
        (s - a >= E.TRANSITION_DEBOUNCE_MS || t.state === "working" || t.state === "offline" || n === "offline" || !i.isMoving()) && (this.handleStateTransition(i, n, t.state), this.lastTransitionTime.set(i.agentId, s));
      }
      for (const s of this.objects)
        s.config.type === "monitor" && s.config.id === `monitor_${t.id}` && s.setGlow(t.state === "working");
    }
  }
  autoSpawnCitizen(e) {
    const t = this.config.defaultSprites ?? ["nova", "rio", "dexter", "morty"], i = t[this.autoSpawnIndex % t.length];
    this.autoSpawnIndex++;
    const s = [...this.typedLocations.filter((o) => o.type === "wander")].sort(() => Math.random() - 0.5);
    let a = s.find((o) => this.reservation.isAvailable(o.x, o.y, e.id)) ?? s[0] ?? null;
    !a && this.typedLocations.length > 0 && (a = [...this.typedLocations].sort(() => Math.random() - 0.5).find((h) => this.reservation.isAvailable(h.x, h.y, e.id)) ?? null);
    let r;
    if (a)
      r = a.name, this.reservation.reserve(a.x, a.y, e.id);
    else {
      const o = this.scene.pathfinder.getWalkableTiles();
      let h;
      if (o.length > 0) {
        const c = Math.max(1, Math.floor(o.length / 8)), l = this.autoSpawnIndex * c % o.length;
        for (let p = 0; p < o.length; p++) {
          const f = (l + p) % o.length, d = o[f];
          if (this.reservation.isAvailable(d.x, d.y, e.id)) {
            h = d;
            break;
          }
        }
        h = h ?? o[l];
      }
      h ? (r = `_spawn_${h.x}_${h.y}`, this.scene.config.locations[r] = { x: h.x, y: h.y, label: r }, this.reservation.reserve(h.x, h.y, e.id)) : r = "center";
    }
    this.spawningAgents.add(e.id), this.addCitizen({ agentId: e.id, name: e.name, sprite: i, position: r }).then((o) => {
      o.updateState(e.state, e.task, e.energy);
    }).catch(() => {
    }).finally(() => {
      this.spawningAgents.delete(e.id);
    });
  }
  /** Returns anchor names assigned as home positions to other citizens */
  getOtherHomeAnchors(e) {
    const t = /* @__PURE__ */ new Set();
    for (const i of this.citizens)
      i.agentId !== e && t.add(i.getHomePosition());
    return t;
  }
  handleStateTransition(e, t, i) {
    const n = this.getOtherHomeAnchors(e.agentId);
    if (this.typedLocations.length > 0)
      if (i === "working") {
        const s = e.getHomePosition();
        this.typedLocations.find((a) => a.name === s), e.goToAnchor(s, this.typedLocations, this.scene.pathfinder, this.reservation) || e.goToAnchorType("work", this.typedLocations, this.scene.pathfinder, this.reservation, n);
      } else i === "sleeping" ? e.goToAnchorType("rest", this.typedLocations, this.scene.pathfinder, this.reservation, n) : i === "speaking" ? e.isMoving() || e.goToAnchorType("social", this.typedLocations, this.scene.pathfinder, this.reservation, n) : i === "thinking" && e.goToAnchorType("utility", this.typedLocations, this.scene.pathfinder, this.reservation, n);
    i === "working" && e.task ? this.speechBubbles.show(e.x + 16, e.y - 8, e.task, 4, e) : i === "error" ? this.particles.emitExclamation(e.x + 16, e.y - e.getSittingOffset()) : i === "speaking" && e.task && this.speechBubbles.show(e.x + 16, e.y - 8, e.task, 5, e);
  }
  updateCitizenEffects(e, t) {
    const i = e.agentId, n = (this.particleTimers.get(i) ?? 0) + t;
    this.particleTimers.set(i, n), e.state === "sleeping" && n > 1.5 && (this.particleTimers.set(i, 0), this.particles.emitZzz(e.x + 16, e.y)), e.state === "thinking" && n > 2 && (this.particleTimers.set(i, 0), this.particles.emitThought(e.x + 16, e.y)), e.state === "error" && n > 2 && (this.particleTimers.set(i, 0), this.particles.emitExclamation(e.x + 16, e.y));
  }
  handleClick(e) {
    const t = this.renderer.screenToWorld(e.offsetX, e.offsetY);
    for (const i of this.citizens)
      if (i.containsPoint(t.x, t.y)) {
        this.emit("citizen:click", {
          agentId: i.agentId,
          name: i.name,
          state: i.state,
          task: i.task,
          energy: i.energy
        });
        return;
      }
    for (const i of this.objects)
      if (i.containsPoint(t.x, t.y)) {
        this.emit("object:click", { id: i.config.id, type: i.config.type });
        return;
      }
  }
};
E.TRANSITION_DEBOUNCE_MS = 8e3;
let W = E;
function re() {
  const t = [], i = [];
  for (let n = 0; n < 12; n++) {
    t[n] = [], i[n] = [];
    for (let s = 0; s < 16; s++)
      n === 0 || n === 11 || s === 0 || s === 15 ? (t[n][s] = "floor", i[n][s] = !1) : (t[n][s] = "floor", i[n][s] = !0);
  }
  return i[2][2] = !1, i[2][3] = !1, i[2][6] = !1, i[2][7] = !1, {
    name: "main",
    tileWidth: 32,
    tileHeight: 32,
    layers: [t],
    walkable: i,
    locations: {
      desk_1: { x: 3, y: 3, label: "Desk 1" },
      desk_2: { x: 7, y: 3, label: "Desk 2" },
      coffee_machine: { x: 12, y: 2, label: "Coffee Machine" },
      couch: { x: 10, y: 8, label: "Couch" },
      whiteboard: { x: 7, y: 1, label: "Whiteboard" },
      intercom: { x: 1, y: 1, label: "Intercom" },
      center: { x: 7, y: 6, label: "Center" }
    },
    tiles: {
      floor: "tiles/office.png"
    }
  };
}
function O(x) {
  return {
    sheets: {
      walk: `/universal_assets/citizens/${x}_walk.png`,
      actions: `/universal_assets/citizens/${x}_actions.png`
    },
    animations: {
      idle_down: { sheet: "actions", row: 3, frames: 4, speed: 0.5 },
      idle_up: { sheet: "actions", row: 3, frames: 4, speed: 0.5 },
      walk_down: { sheet: "walk", row: 0, frames: 4, speed: 0.15 },
      walk_up: { sheet: "walk", row: 1, frames: 4, speed: 0.15 },
      walk_left: { sheet: "walk", row: 2, frames: 4, speed: 0.15 },
      walk_right: { sheet: "walk", row: 3, frames: 4, speed: 0.15 },
      working: { sheet: "actions", row: 0, frames: 4, speed: 0.3 },
      sleeping: { sheet: "actions", row: 1, frames: 2, speed: 0.8 },
      talking: { sheet: "actions", row: 2, frames: 4, speed: 0.15 }
    },
    frameWidth: 64,
    frameHeight: 64
  };
}
const he = /* @__PURE__ */ Object.freeze(/* @__PURE__ */ Object.defineProperty({
  __proto__: null,
  ANCHOR_COLORS: M,
  ANCHOR_TYPES: $,
  Animator: X,
  Camera: K,
  Citizen: D,
  CitizenLayer: J,
  DEADSPACE: Y,
  Editor: ae,
  InteractiveObject: Z,
  Miniverse: W,
  ParticleSystem: V,
  Pathfinder: N,
  PropSystem: oe,
  Renderer: G,
  Scene: U,
  Signal: ee,
  SpeechBubbleSystem: Q,
  SpriteSheet: _,
  TileReservation: q,
  createStandardSpriteConfig: O
}, Symbol.toStringTag, { value: "Module" }));
export {
  M as ANCHOR_COLORS,
  $ as ANCHOR_TYPES,
  X as Animator,
  K as Camera,
  D as Citizen,
  J as CitizenLayer,
  Y as DEADSPACE,
  ae as Editor,
  Z as InteractiveObject,
  W as Miniverse,
  V as ParticleSystem,
  N as Pathfinder,
  oe as PropSystem,
  G as Renderer,
  U as Scene,
  ee as Signal,
  Q as SpeechBubbleSystem,
  _ as SpriteSheet,
  q as TileReservation,
  O as createStandardSpriteConfig
};
