// Blocky 3D view for the buyer demo: a voxel trading-floor island where a
// buyer agent and a seller agent act out the same events as the flat view.
// All textures are drawn here, pixel by pixel, on small canvases.

import * as THREE from "./three.module.min.js";

const BUYER_X = -4;
const SELLER_X = 3.9;
const STALL_X = 2.6;
const GROUND = 0.5;
const TONE_COLOR = { ok: 0x35d07f, counter: 0xf0a92e, pending: 0xf0a92e, refused: 0xef5a5a };

// ---------------------------------------------------------------------------
// Pixel-art textures
// ---------------------------------------------------------------------------

function rng(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function texture(w, h, draw, seed = 1) {
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d");
  ctx.imageSmoothingEnabled = false;
  draw(ctx, rng(seed), w, h);
  const tex = new THREE.CanvasTexture(canvas);
  tex.magFilter = THREE.NearestFilter;
  tex.minFilter = THREE.NearestFilter;
  tex.generateMipmaps = false;
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

function speckle(ctx, r, w, h, palette, x0 = 0, y0 = 0) {
  for (let y = y0; y < h; y++) {
    for (let x = x0; x < w; x++) {
      ctx.fillStyle = palette[Math.floor(r() * palette.length)];
      ctx.fillRect(x, y, 1, 1);
    }
  }
}

function px(ctx, color, x, y, w = 1, h = 1) {
  ctx.fillStyle = color;
  ctx.fillRect(x, y, w, h);
}

const PAL = {
  grass: ["#5d9c3a", "#6aac44", "#4f8a31", "#78b84f", "#62a33d"],
  dirt: ["#7a5534", "#8a6240", "#6b4a2d", "#94704a"],
  stone: ["#7d7d7d", "#8c8c8c", "#6e6e6e", "#999999", "#757575"],
  plank: ["#a8824f", "#b58d58", "#9c7646"],
  leaves: ["#3f7d2a", "#4a8f33", "#356b23", "#58a03d"],
  gold: ["#f4c430", "#ffd84a", "#e2b021", "#ffe680"],
};

function makeBlockTextures() {
  const grassTop = texture(16, 16, (c, r) => speckle(c, r, 16, 16, PAL.grass), 11);
  const dirt = texture(16, 16, (c, r) => speckle(c, r, 16, 16, PAL.dirt), 12);
  const grassSide = texture(
    16,
    16,
    (c, r) => {
      speckle(c, r, 16, 16, PAL.dirt);
      for (let x = 0; x < 16; x++) {
        const depth = 3 + Math.floor(r() * 3);
        for (let y = 0; y < depth; y++) px(c, PAL.grass[Math.floor(r() * PAL.grass.length)], x, y);
      }
    },
    13,
  );
  const stone = texture(
    16,
    16,
    (c, r) => {
      speckle(c, r, 16, 16, PAL.stone);
      for (let i = 0; i < 5; i++) px(c, "#5f5f5f", Math.floor(r() * 14), Math.floor(r() * 15), 2, 1);
    },
    14,
  );
  const plank = texture(
    16,
    16,
    (c, r) => {
      speckle(c, r, 16, 16, PAL.plank);
      for (const y of [3, 7, 11, 15]) px(c, "#6f512e", 0, y, 16, 1);
      for (const [x, y] of [[5, 0], [12, 4], [3, 8], [9, 12]]) px(c, "#6f512e", x, y, 1, 3);
    },
    15,
  );
  const logSide = texture(
    16,
    16,
    (c, r) => {
      for (let x = 0; x < 16; x++) {
        const shade = ["#5b3f22", "#6b4a29", "#4d351c"][Math.floor(r() * 3)];
        px(c, shade, x, 0, 1, 16);
      }
    },
    16,
  );
  const logTop = texture(
    16,
    16,
    (c) => {
      px(c, "#5b3f22", 0, 0, 16, 16);
      px(c, "#b48a55", 2, 2, 12, 12);
      px(c, "#9c7646", 4, 4, 8, 8);
      px(c, "#b48a55", 6, 6, 4, 4);
    },
    17,
  );
  const leaves = texture(16, 16, (c, r) => speckle(c, r, 16, 16, PAL.leaves), 18);
  return { grassTop, grassSide, dirt, stone, plank, logSide, logTop, leaves };
}

function goldTexture() {
  return texture(
    16,
    16,
    (c, r) => {
      speckle(c, r, 16, 16, PAL.gold);
      px(c, "#fff3b0", 1, 1, 14, 1);
      px(c, "#fff3b0", 1, 1, 1, 14);
      px(c, "#b8860b", 1, 14, 14, 1);
      px(c, "#b8860b", 14, 1, 1, 14);
      px(c, "#fff8d0", 4, 4, 3, 1);
    },
    21,
  );
}

function lockTexture() {
  return texture(
    16,
    16,
    (c, r) => {
      speckle(c, r, 16, 16, ["#d98a1c", "#e39a2a", "#c97c16"]);
      px(c, "#a3341f", 0, 0, 16, 2);
      px(c, "#a3341f", 0, 14, 16, 2);
      px(c, "#a3341f", 0, 0, 2, 16);
      px(c, "#a3341f", 14, 0, 2, 16);
      px(c, "#2b1a0c", 6, 4, 4, 4);
      px(c, "#2b1a0c", 7, 8, 2, 4);
    },
    22,
  );
}

function signTexture(text) {
  return texture(
    64,
    16,
    (c, r) => {
      speckle(c, r, 64, 16, PAL.plank);
      px(c, "#6f512e", 0, 0, 64, 1);
      px(c, "#6f512e", 0, 15, 64, 1);
      c.fillStyle = "#2b1a0c";
      c.font = "bold 11px monospace";
      c.textAlign = "center";
      c.textBaseline = "middle";
      c.fillText(text, 32, 8.5);
    },
    23,
  );
}

function stripeTexture() {
  return texture(16, 16, (c) => {
    for (let x = 0; x < 16; x += 4) {
      px(c, "#3f8f3a", x, 0, 2, 16);
      px(c, "#f2efe2", x + 2, 0, 2, 16);
    }
  });
}

function glowTexture(color) {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 64;
  const ctx = canvas.getContext("2d");
  const g = ctx.createRadialGradient(32, 32, 2, 32, 32, 32);
  g.addColorStop(0, color);
  g.addColorStop(1, "rgba(0,0,0,0)");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, 64, 64);
  return new THREE.CanvasTexture(canvas);
}

function crossTexture() {
  return texture(16, 16, (c) => {
    for (let i = 1; i < 15; i++) {
      px(c, "#ef3b3b", i, i, 2, 2);
      px(c, "#ef3b3b", 14 - i, i, 2, 2);
    }
  });
}

// ---------------------------------------------------------------------------
// Characters
// ---------------------------------------------------------------------------

const SKINS = {
  buyer: {
    skin: "#e0ac7d",
    hair: "#3b2a1a",
    eye: "#2c5aa0",
    suit: ["#2f4f9e", "#2a4890", "#3456a8"],
    shirt: "#f4f4f4",
    tie: "#c0392b",
    legs: ["#1e2a4a", "#222f52"],
    shoes: "#141414",
    hat: null,
  },
  seller: {
    skin: "#c98e62",
    hair: "#2b1d12",
    eye: "#3b7a2a",
    suit: ["#3f8f3a", "#367d32", "#46a040"],
    shirt: "#e9e2c4",
    tie: null,
    legs: ["#5b4630", "#4f3d2a"],
    shoes: "#2a1d10",
    hat: "#2e6b2a",
  },
};

function partMaterials(faces) {
  // BoxGeometry face order: +x, -x, +y, -y, +z (front), -z (back)
  return faces.map((tex) => new THREE.MeshLambertMaterial({ map: tex }));
}

function headTextures(s, seed) {
  const plain = (fill) => texture(8, 8, (c, r) => speckle(c, r, 8, 8, fill), seed++);
  const hairPal = [s.hair, s.hair, "#4a3624"];
  const front = texture(
    8,
    8,
    (c, r) => {
      speckle(c, r, 8, 8, [s.skin, s.skin, "#d19a6c"]);
      px(c, s.hat || s.hair, 0, 0, 8, 2);
      if (s.hat) px(c, "#1f4d1c", 0, 2, 8, 1);
      px(c, "#ffffff", 1, 4, 2, 1);
      px(c, "#ffffff", 5, 4, 2, 1);
      px(c, s.eye, 2, 4, 1, 1);
      px(c, s.eye, 5, 4, 1, 1);
      px(c, "#8a4b3a", 3, 6, 2, 1);
    },
    seed++,
  );
  const side = texture(
    8,
    8,
    (c, r) => {
      speckle(c, r, 8, 8, [s.skin, s.skin, "#d19a6c"]);
      speckle(c, r, 8, 3, s.hat ? [s.hat] : hairPal);
    },
    seed++,
  );
  const top = s.hat ? plain([s.hat, "#2a6226"]) : plain(hairPal);
  const back = plain(s.hat ? [s.hat] : hairPal);
  const bottom = plain([s.skin]);
  return [side, side, top, bottom, front, back];
}

function bodyTextures(s, seed) {
  const suit = texture(8, 12, (c, r) => speckle(c, r, 8, 12, s.suit), seed++);
  const front = texture(
    8,
    12,
    (c, r) => {
      speckle(c, r, 8, 12, s.suit);
      if (s.tie) {
        px(c, s.shirt, 3, 0, 2, 5);
        px(c, s.shirt, 2, 0, 1, 2);
        px(c, s.shirt, 5, 0, 1, 2);
        px(c, s.tie, 3, 1, 2, 6);
      } else {
        px(c, s.shirt, 1, 4, 6, 8);
        px(c, "#d3caa6", 2, 7, 4, 3);
        px(c, s.shirt, 3, 0, 2, 2);
      }
    },
    seed++,
  );
  return [suit, suit, suit, suit, front, suit];
}

function armTextures(s, seed) {
  const sleeve = texture(
    4,
    12,
    (c, r) => {
      speckle(c, r, 4, 12, s.suit);
      px(c, s.skin, 0, 9, 4, 3);
    },
    seed++,
  );
  const top = texture(4, 4, (c, r) => speckle(c, r, 4, 4, s.suit), seed++);
  const hand = texture(4, 4, (c) => px(c, s.skin, 0, 0, 4, 4), seed++);
  return [sleeve, sleeve, top, hand, sleeve, sleeve];
}

function legTextures(s, seed) {
  const leg = texture(
    4,
    12,
    (c, r) => {
      speckle(c, r, 4, 12, s.legs);
      px(c, s.shoes, 0, 10, 4, 2);
    },
    seed++,
  );
  const top = texture(4, 4, (c, r) => speckle(c, r, 4, 4, s.legs), seed++);
  const sole = texture(4, 4, (c) => px(c, s.shoes, 0, 0, 4, 4), seed++);
  return [leg, leg, top, sole, leg, leg];
}

function makeCharacter(kind) {
  const s = SKINS[kind];
  const seed = kind === "buyer" ? 100 : 200;
  const root = new THREE.Group();
  const body = new THREE.Group();
  root.add(body);

  const mk = (w, h, d, mats) => new THREE.Mesh(new THREE.BoxGeometry(w, h, d), partMaterials(mats));

  const legL = mk(0.25, 0.75, 0.25, legTextures(s, seed));
  const legR = mk(0.25, 0.75, 0.25, legTextures(s, seed + 5));
  legL.position.set(-0.125, 0.375, 0);
  legR.position.set(0.125, 0.375, 0);
  const torso = mk(0.5, 0.75, 0.25, bodyTextures(s, seed + 10));
  torso.position.set(0, 1.125, 0);
  const head = mk(0.5, 0.5, 0.5, headTextures(s, seed + 20));
  head.position.set(0, 1.75, 0);

  const arm = (side, seedOff) => {
    const pivot = new THREE.Group();
    pivot.position.set(side * 0.375, 1.5, 0);
    const mesh = mk(0.25, 0.75, 0.25, armTextures(s, seed + seedOff));
    mesh.position.y = -0.375;
    pivot.add(mesh);
    return pivot;
  };
  const armL = arm(-1, 30);
  const armR = arm(1, 40);
  body.add(legL, legR, torso, head, armL, armR);

  if (kind === "buyer") {
    const caseMats = partMaterials(
      Array(6).fill(
        texture(8, 8, (c, r) => {
          speckle(c, r, 8, 8, ["#6b3f1d", "#7a4a24", "#5e3518"]);
          px(c, "#c9a227", 3, 1, 2, 1);
        }, 301),
      ),
    );
    const briefcase = new THREE.Mesh(new THREE.BoxGeometry(0.12, 0.3, 0.42), caseMats);
    briefcase.position.set(0.05, -0.82, 0);
    armR.add(briefcase);
  }

  const shadow = new THREE.Mesh(
    new THREE.CircleGeometry(0.45, 16),
    new THREE.MeshBasicMaterial({ color: 0x000000, transparent: true, opacity: 0.25 }),
  );
  shadow.rotation.x = -Math.PI / 2;
  shadow.position.y = 0.01;
  root.add(shadow);

  return {
    root,
    body,
    head,
    armL,
    armR,
    talkUntil: 0,
    thinkUntil: 0,
    refuseUntil: 0,
    refuseStart: 0,
  };
}

// ---------------------------------------------------------------------------
// The island
// ---------------------------------------------------------------------------

function buildIsland(scene, tex) {
  const lambert = (map) => new THREE.MeshLambertMaterial({ map });
  const mats = {
    grass: [tex.grassSide, tex.grassSide, tex.grassTop, tex.dirt, tex.grassSide, tex.grassSide].map(lambert),
    dirt: lambert(tex.dirt),
    stone: lambert(tex.stone),
    plank: lambert(tex.plank),
    log: [tex.logSide, tex.logSide, tex.logTop, tex.logTop, tex.logSide, tex.logSide].map(lambert),
    leaves: lambert(tex.leaves),
  };
  const cells = { grass: [], dirt: [], stone: [], plank: [], log: [], leaves: [] };
  const r = rng(7);
  const W = 7;
  const D = 4;
  for (let x = -W; x <= W; x++) {
    for (let z = -D; z <= D; z++) {
      const edge = Math.max(Math.abs(x) / W, Math.abs(z) / D);
      if (edge >= 1 && r() < 0.45) continue;
      const floor = Math.abs(x) <= 2 && Math.abs(z) <= 2;
      cells[floor ? "plank" : "grass"].push([x, 0, z]);
      cells.dirt.push([x, -1, z]);
      const depth = Math.floor((1 - edge) * 4 + r() * 1.5);
      for (let y = 2; y <= 1 + depth; y++) cells[y < 3 ? "dirt" : "stone"].push([x, -y, z]);
    }
  }
  for (const [tx, tz] of [[-6, -3], [6, -3], [-2, -4]]) {
    for (let y = 1; y <= 3; y++) cells.log.push([tx, y, tz]);
    for (let dx = -1; dx <= 1; dx++) {
      for (let dz = -1; dz <= 1; dz++) {
        cells.leaves.push([tx + dx, 4, tz + dz]);
        if (Math.abs(dx) + Math.abs(dz) < 2) cells.leaves.push([tx + dx, 5, tz + dz]);
      }
    }
  }
  const geo = new THREE.BoxGeometry(1, 1, 1);
  const m = new THREE.Matrix4();
  for (const [name, list] of Object.entries(cells)) {
    if (!list.length) continue;
    const mesh = new THREE.InstancedMesh(geo, mats[name], list.length);
    list.forEach(([x, y, z], i) => mesh.setMatrixAt(i, m.makeTranslation(x, y, z)));
    mesh.instanceMatrix.needsUpdate = true;
    scene.add(mesh);
  }
}

function buildStall(scene, tex) {
  const group = new THREE.Group();
  group.position.set(STALL_X, GROUND, 0);
  const plank = new THREE.MeshLambertMaterial({ map: tex.plank });
  const log = new THREE.MeshLambertMaterial({ map: tex.logSide });
  const counter = new THREE.Mesh(new THREE.BoxGeometry(0.9, 1.0, 2.4), plank);
  counter.position.y = 0.5;
  group.add(counter);
  for (const x of [-0.38, 0.38]) {
    for (const z of [-1.15, 1.15]) {
      const post = new THREE.Mesh(new THREE.BoxGeometry(0.14, 3.0, 0.14), log);
      post.position.set(x, 1.5, z);
      group.add(post);
    }
  }
  const awning = new THREE.Mesh(
    new THREE.BoxGeometry(1.3, 0.14, 2.7),
    new THREE.MeshLambertMaterial({ map: stripeTexture() }),
  );
  awning.position.set(-0.1, 3.05, 0);
  awning.rotation.z = 0.22;
  group.add(awning);
  const signFace = new THREE.MeshLambertMaterial({ map: signTexture("SELLER") });
  const signMats = [plank, plank, plank, plank, signFace, signFace];
  const sign = new THREE.Mesh(new THREE.BoxGeometry(1.6, 0.4, 0.08), signMats);
  sign.position.set(-0.05, 2.6, 1.24);
  group.add(sign);
  scene.add(group);
  return { group, counterTop: GROUND + 1.0 };
}

// ---------------------------------------------------------------------------
// The view
// ---------------------------------------------------------------------------

export function createVoxelView({ container, labels, getInsets, reducedMotion, isCompact }) {
  const renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "low-power" });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  container.prepend(renderer.domElement);
  renderer.domElement.className = "scene-canvas";

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x9fd3ff);
  scene.fog = new THREE.Fog(0x9fd3ff, 45, 95);
  scene.add(new THREE.HemisphereLight(0xdff1ff, 0x5a4a3a, 1.2));
  const sun = new THREE.DirectionalLight(0xffffff, 1.6);
  sun.position.set(6, 12, 8);
  scene.add(sun);

  const tex = makeBlockTextures();
  buildIsland(scene, tex);
  const stall = buildStall(scene, tex);

  const clouds = new THREE.InstancedMesh(
    new THREE.BoxGeometry(1, 1, 1),
    new THREE.MeshLambertMaterial({ color: 0xffffff, transparent: true, opacity: 0.85 }),
    6,
  );
  const cloudData = [];
  {
    const r = rng(9);
    for (let i = 0; i < 6; i++) {
      cloudData.push({
        x: -20 + r() * 40,
        y: 9 + r() * 3,
        z: -14 + r() * 10,
        sx: 3 + r() * 4,
        sz: 2 + r() * 2,
        v: 0.2 + r() * 0.3,
      });
    }
  }
  scene.add(clouds);

  const buyer = makeCharacter("buyer");
  buyer.root.position.set(BUYER_X, GROUND, 0);
  buyer.root.rotation.y = Math.PI / 2;
  const seller = makeCharacter("seller");
  seller.root.position.set(SELLER_X, GROUND, 0);
  seller.root.rotation.y = -Math.PI / 2;
  scene.add(buyer.root, seller.root);

  // Packet
  const packet = new THREE.Group();
  const packetCore = new THREE.Mesh(
    new THREE.BoxGeometry(0.24, 0.24, 0.24),
    new THREE.MeshBasicMaterial({ color: 0x7fd4ff }),
  );
  const packetGlow = new THREE.Sprite(
    new THREE.SpriteMaterial({
      map: glowTexture("rgba(140,210,255,1)"),
      blending: THREE.AdditiveBlending,
      depthWrite: false,
      transparent: true,
    }),
  );
  packetGlow.scale.set(1.1, 1.1, 1);
  packet.add(packetCore, packetGlow);
  packet.visible = false;
  scene.add(packet);
  const fromBuyer = new THREE.Vector3(BUYER_X + 0.5, GROUND + 1.3, 0);
  const toSeller = new THREE.Vector3(SELLER_X - 0.6, GROUND + 1.6, 0);
  const flight = { phase: "idle", t0: 0, dur: 0.7 };

  // Refusal X
  const crossMat = new THREE.SpriteMaterial({ map: crossTexture(), transparent: true, depthWrite: false });
  const crosses = [];

  // Items
  const goldMat = new THREE.MeshLambertMaterial({ map: goldTexture(), emissive: 0x4a3600 });
  const lockMat = new THREE.MeshLambertMaterial({ map: lockTexture(), emissive: 0x2a1200 });
  const deals = [];
  const locks = [];
  const pops = [];

  // ---- HTML labels -------------------------------------------------------
  const tracked = [];
  function label(className, anchor, text) {
    const el = document.createElement("div");
    el.className = "label3d " + className;
    if (text != null) el.textContent = text;
    el.style.visibility = "hidden"; // until placeLabels() has positioned it
    labels.append(el);
    const entry = { el, anchor };
    tracked.push(entry);
    return entry;
  }
  function dropLabel(entry) {
    if (!entry) return;
    entry.el.remove();
    const i = tracked.indexOf(entry);
    if (i >= 0) tracked.splice(i, 1);
  }
  const buyerTag = label("nametag buyer", new THREE.Vector3(BUYER_X, GROUND + 2.35, 0), "Buyer agent");
  label("nametag seller", new THREE.Vector3(SELLER_X, GROUND + 2.35, 0), "Seller agent");
  let buyerBubble = null;
  let sellerBubble = null;
  let tags = [];

  function bubble(side, text, tone, meta) {
    const anchor =
      side === "buyer"
        ? new THREE.Vector3(BUYER_X + 0.6, GROUND + 3.4, 0)
        : new THREE.Vector3(SELLER_X - 0.6, GROUND + 3.4, 0);
    const entry = label("bubble3d " + side + (tone ? " tone-" + tone : ""), anchor);
    const line = document.createElement("div");
    line.textContent = text;
    entry.el.append(line);
    if (meta) {
      const m = document.createElement("div");
      m.className = "meta3d";
      m.textContent = meta;
      entry.el.append(m);
    }
    return entry;
  }

  function addTag(kind, amount, rung) {
    if (tags.length >= 8) dropLabel(tags.shift());
    const x = kind === "bid" ? -1.2 : 1.0;
    const entry = label(
      "pricetag " + kind,
      new THREE.Vector3(x, GROUND + 1.1 + rung * 0.55, 0.6),
      (kind === "bid" ? "bid " : kind === "quote" ? "quote " : "ask ") + amount,
    );
    tags.push(entry);
  }
  let rung = 0;

  // ---- camera ------------------------------------------------------------
  const camera = new THREE.PerspectiveCamera(32, 1, 0.1, 200);
  const target = new THREE.Vector3(0, 1.0, 0.4);
  // radius = fit (distance that frames both agents in the visible area) * zoom
  const orbit = { az: -0.12, el: 0.48, fit: 27, zoom: 1, drift: 0, lastInput: -10 };
  const LIMITS = { az: [-0.95, 0.95], el: [0.25, 1.0], zoom: [0.55, 1.6] };
  const clamp = (v, [a, b]) => Math.min(b, Math.max(a, v));

  // One pointer rotates, two pointers pinch-zoom.
  const pointers = new Map();
  let dragging = null;
  let pinch = null;
  const canvas = renderer.domElement;
  const spread = () => {
    const [a, b] = [...pointers.values()];
    return Math.hypot(a.x - b.x, a.y - b.y) || 1;
  };
  canvas.addEventListener("pointerdown", (e) => {
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    canvas.setPointerCapture(e.pointerId);
    if (pointers.size === 2) {
      dragging = null;
      pinch = { d: spread(), zoom: orbit.zoom };
    } else if (pointers.size === 1) {
      dragging = { x: e.clientX, y: e.clientY };
    }
  });
  canvas.addEventListener("pointermove", (e) => {
    if (!pointers.has(e.pointerId)) return;
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pinch && pointers.size >= 2) {
      orbit.zoom = clamp((pinch.zoom * pinch.d) / spread(), LIMITS.zoom);
      orbit.lastInput = clock.getElapsedTime();
      return;
    }
    if (!dragging) return;
    orbit.az = clamp(orbit.az - (e.clientX - dragging.x) * 0.004, LIMITS.az);
    orbit.el = clamp(orbit.el + (e.clientY - dragging.y) * 0.003, LIMITS.el);
    dragging = { x: e.clientX, y: e.clientY };
    orbit.lastInput = clock.getElapsedTime();
  });
  const endPointer = (e) => {
    pointers.delete(e.pointerId);
    if (pointers.size < 2) pinch = null;
    const rest = [...pointers.values()][0];
    dragging = rest ? { x: rest.x, y: rest.y } : null;
  };
  canvas.addEventListener("pointerup", endPointer);
  canvas.addEventListener("pointercancel", endPointer);
  canvas.addEventListener(
    "wheel",
    (e) => {
      e.preventDefault();
      orbit.zoom = clamp(orbit.zoom * (1 + Math.sign(e.deltaY) * 0.08), LIMITS.zoom);
      orbit.lastInput = clock.getElapsedTime();
    },
    { passive: false },
  );

  let inset = { top: 0, right: 0, bottom: 0 };
  function resize() {
    const w = container.clientWidth || window.innerWidth;
    const h = container.clientHeight || window.innerHeight;
    renderer.setSize(w, h, false);
    inset = { top: 0, right: 0, bottom: 0, ...(getInsets ? getInsets() : {}) };
    const regionW = Math.max(1, w - inset.right);
    const regionH = Math.max(1, h - inset.top - inset.bottom);
    const aspect = regionW / regionH;
    // Portrait phones get a wider lens so both agents stay in frame.
    camera.fov = aspect < 1 ? 42 : 32;
    camera.aspect = w / h;
    const tanHalf = Math.tan(THREE.MathUtils.degToRad(camera.fov / 2));
    const regionTan = tanHalf * (regionH / h);
    orbit.fit = Math.max(18, (aspect < 1 ? 9 : 7.2) / (regionTan * aspect), 5.6 / regionTan);
    camera.setViewOffset(w, h, inset.right / 2, -(inset.top - inset.bottom) / 2, w, h);
    camera.updateProjectionMatrix();
  }
  window.addEventListener("resize", resize);

  // ---- animation -----------------------------------------------------------
  const clock = new THREE.Clock();
  const v = new THREE.Vector3();
  let running = false;
  let frameHandle = 0;

  function arc(a, b, t, lift) {
    v.lerpVectors(a, b, t);
    v.y += Math.sin(Math.PI * t) * lift;
    return v;
  }

  function animateCharacter(ch, t) {
    const talking = t < ch.talkUntil;
    const thinking = t < ch.thinkUntil;
    const refusing = t < ch.refuseUntil;
    const motion = reducedMotion ? 0 : 1;
    ch.body.position.y = thinking ? Math.abs(Math.sin(t * 6)) * 0.08 * motion : 0;
    if (refusing) {
      ch.armL.rotation.set(-1.25, 0, 0.75);
      ch.armR.rotation.set(-1.25, 0, -0.75);
      ch.head.rotation.y = Math.sin((t - ch.refuseStart) * 14) * 0.45 * motion;
    } else if (talking) {
      const swing = Math.sin(t * 9) * 0.6 * motion;
      ch.armL.rotation.set(swing - 0.2, 0, 0);
      ch.armR.rotation.set(-swing - 0.2, 0, 0);
      ch.head.rotation.y = Math.sin(t * 3) * 0.12 * motion;
    } else {
      const idle = Math.sin(t * 1.5) * 0.04 * motion;
      ch.armL.rotation.set(idle, 0, 0.02);
      ch.armR.rotation.set(-idle, 0, -0.02);
      ch.head.rotation.y *= 0.85;
    }
  }

  function frame() {
    if (!running) return;
    frameHandle = requestAnimationFrame(frame);
    const t = clock.getElapsedTime();

    if (!reducedMotion && t - orbit.lastInput > 4) orbit.drift = Math.sin(t * 0.07) * 0.12;
    else orbit.drift *= 0.95;
    const az = orbit.az + orbit.drift;
    const radius = orbit.fit * orbit.zoom;
    camera.position.set(
      target.x + radius * Math.cos(orbit.el) * Math.sin(az),
      target.y + radius * Math.sin(orbit.el),
      target.z + radius * Math.cos(orbit.el) * Math.cos(az),
    );
    camera.lookAt(target);

    animateCharacter(buyer, t);
    animateCharacter(seller, t);

    if (flight.phase !== "idle") {
      const k = Math.min(1, (t - flight.t0) / flight.dur);
      if (flight.phase === "out") {
        packet.position.copy(arc(fromBuyer, toSeller, k, 2.2));
        if (k >= 1) flight.phase = "wait";
      } else if (flight.phase === "wait") {
        packet.position.copy(toSeller);
        packet.position.y += Math.sin(t * 8) * 0.08;
      } else if (flight.phase === "back") {
        packet.position.copy(arc(toSeller, fromBuyer, k, 1.6));
        if (k >= 1) {
          flight.phase = "idle";
          packet.visible = false;
        }
      }
      packetCore.rotation.set(t * 3, t * 2, 0);
    }

    for (let i = pops.length - 1; i >= 0; i--) {
      const p = pops[i];
      const k = Math.min(1, (t - p.t0) / 0.35);
      const s = reducedMotion ? 1 : k < 1 ? 1.25 * Math.sin((k * Math.PI) / 1.4) : 1;
      p.mesh.scale.setScalar(Math.max(0.01, s));
      if (k >= 1) {
        p.mesh.scale.setScalar(1);
        pops.splice(i, 1);
      }
    }
    for (const g of deals) g.mesh.rotation.y = reducedMotion ? 0.3 : t * 0.6;

    for (let i = crosses.length - 1; i >= 0; i--) {
      const c = crosses[i];
      const k = (t - c.t0) / 1.3;
      c.sprite.position.y = c.y0 + k * (reducedMotion ? 0 : 0.9);
      c.sprite.material.opacity = Math.max(0, 1 - k);
      if (k >= 1) {
        scene.remove(c.sprite);
        crosses.splice(i, 1);
      }
    }

    const m = new THREE.Matrix4();
    cloudData.forEach((c, i) => {
      const x = reducedMotion ? c.x : ((c.x + t * c.v + 30) % 60) - 30;
      m.compose(new THREE.Vector3(x, c.y, c.z), new THREE.Quaternion(), new THREE.Vector3(c.sx, 0.6, c.sz));
      clouds.setMatrixAt(i, m);
    });
    clouds.instanceMatrix.needsUpdate = true;

    renderer.render(scene, camera);
    placeLabels();
  }

  function placeLabels() {
    const w = canvas.clientWidth;
    const h = canvas.clientHeight;
    const pad = 6;
    const box = {
      left: pad,
      right: w - inset.right - pad,
      top: inset.top + pad,
      bottom: h - inset.bottom - pad,
    };
    // Read every size first, then write, to avoid forcing layout per label.
    const placed = tracked.map((entry) => {
      v.copy(entry.anchor).project(camera);
      if (v.z > 1 || v.z < -1) return null;
      let x = (v.x * 0.5 + 0.5) * w;
      let y = (-v.y * 0.5 + 0.5) * h;
      const bw = entry.el.offsetWidth;
      x = Math.min(Math.max(x, box.left + bw / 2), Math.max(box.left + bw / 2, box.right - bw / 2));
      if (entry.el.classList.contains("bubble3d")) {
        const bh = entry.el.offsetHeight;
        y = Math.min(Math.max(y, box.top + bh), Math.max(box.top + bh, box.bottom));
      }
      return { x, y };
    });
    tracked.forEach((entry, i) => {
      const p = placed[i];
      if (!p) {
        entry.el.style.display = "none";
        return;
      }
      entry.el.style.display = "";
      entry.el.style.visibility = "";
      entry.el.style.transform = `translate3d(${p.x.toFixed(1)}px, ${p.y.toFixed(1)}px, 0) translate(-50%, -100%)`;
    });
  }

  // ---- events -------------------------------------------------------------
  function roleOf(who) {
    return !who || who === "public" || who === "anonymous" ? "no key" : who;
  }
  function money(value) {
    if (value && typeof value === "object" && "amount_micros" in value) {
      return "$" + (value.amount_micros / 1e6).toFixed(2);
    }
    return null;
  }

  function onCallStart(ev, t) {
    buyerTag.el.textContent = "Buyer agent (" + roleOf(ev.who) + ")";
    dropLabel(buyerBubble);
    dropLabel(sellerBubble);
    sellerBubble = null;
    buyerBubble = bubble("buyer", ev.say || ev.method + " " + ev.path, null, null);
    buyer.talkUntil = t + 0.8;
    seller.thinkUntil = t + 30;
    packetCore.material.color.set(0x7fd4ff);
    packetGlow.material.color.set(0xffffff);
    packet.visible = true;
    flight.phase = reducedMotion ? "wait" : "out";
    flight.t0 = t;
  }

  function onCallEnd(ev, t) {
    seller.thinkUntil = 0;
    seller.talkUntil = t + 0.9;
    const tone = ev.tone || "ok";
    dropLabel(sellerBubble);
    if (isCompact && isCompact()) {
      dropLabel(buyerBubble);
      buyerBubble = null;
    }
    sellerBubble = bubble(
      "seller",
      ev.reply || String(ev.status_code),
      tone,
      `${ev.method} ${ev.path} · ${ev.status_code} · ${ev.latency_ms} ms`,
    );
    const color = TONE_COLOR[tone] || 0x7fd4ff;
    packetCore.material.color.set(color);
    packetGlow.material.color.set(color);
    if (reducedMotion) {
      flight.phase = "idle";
      packet.visible = false;
    } else {
      flight.phase = "back";
      flight.t0 = t;
    }
    if (ev.status_code === 401 || ev.status_code === 403) refuse(t);

    if (ev.path === "/api/v1/quotes" && ev.status_code === 200) {
      const quote = (ev.response && ev.response.quote) || {};
      const price = money((quote.pricing || {}).final_cpm);
      tags.forEach(dropLabel);
      tags = [];
      rung = 0;
      if (price) addTag("quote", price, rung++);
    }
    if (ev.path === "/api/v1/negotiations/messages" && ev.status_code === 200) {
      const round = (ev.response && ev.response.round) || {};
      const bid = money((ev.request_body || {}).buyer_price) || money(round.buyer_price);
      const ask = money(round.seller_price);
      if (bid) addTag("bid", bid, rung);
      if (ask) addTag("ask", ask, rung);
      rung++;
    }
  }

  function refuse(t) {
    seller.refuseUntil = t + 1.4;
    seller.refuseStart = t;
    seller.talkUntil = 0;
    const sprite = new THREE.Sprite(crossMat.clone());
    sprite.scale.set(0.7, 0.7, 1);
    const y0 = GROUND + 2.6;
    sprite.position.set(SELLER_X - 0.3, y0, 0);
    scene.add(sprite);
    crosses.push({ sprite, t0: t, y0 });
  }

  function addDeal(fields, t) {
    if (deals.length >= 6) {
      const old = deals.shift();
      scene.remove(old.mesh);
      dropLabel(old.tag);
    }
    const slot = deals.length;
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(0.36, 0.36, 0.36), goldMat);
    mesh.position.set(STALL_X - 0.1, stall.counterTop + 0.19, -0.85 + slot * 0.34);
    scene.add(mesh);
    pops.push({ mesh, t0: t });
    const tag = label(
      "itemtag deal",
      new THREE.Vector3(mesh.position.x, mesh.position.y + 0.35, mesh.position.z),
      fields.deal_id || "deal",
    );
    deals.push({ mesh, tag });
  }

  function addLock(fields, t) {
    if (locks.length >= 3) {
      const old = locks.shift();
      scene.remove(old.mesh);
      dropLabel(old.tag);
    }
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(0.6, 0.6, 0.6), lockMat);
    mesh.position.set(1.4, GROUND + 0.3, 1.6 - locks.length * 0.85);
    scene.add(mesh);
    pops.push({ mesh, t0: t });
    const tag = label(
      "itemtag lock",
      new THREE.Vector3(mesh.position.x, mesh.position.y + 0.5, mesh.position.z),
      "Held for approval" + (fields.gross ? " · " + fields.gross : ""),
    );
    locks.push({ mesh, tag });
  }

  function onEvent(ev) {
    const t = clock.getElapsedTime();
    switch (ev.type) {
      case "run_start":
        tags.forEach(dropLabel);
        tags = [];
        rung = 0;
        break;
      case "call_start":
        onCallStart(ev, t);
        break;
      case "call_end":
        onCallEnd(ev, t);
        break;
      case "card":
        if (ev.kind === "deal") addDeal(ev.fields || {}, t);
        if (ev.kind === "approval") addLock(ev.fields || {}, t);
        break;
      case "run_end":
      case "run_error":
        seller.thinkUntil = 0;
        if (flight.phase === "wait") {
          flight.phase = "idle";
          packet.visible = false;
        }
        break;
    }
  }

  function clear() {
    for (const d of deals) {
      scene.remove(d.mesh);
      dropLabel(d.tag);
    }
    for (const l of locks) {
      scene.remove(l.mesh);
      dropLabel(l.tag);
    }
    deals.length = 0;
    locks.length = 0;
    tags.forEach(dropLabel);
    tags = [];
    rung = 0;
    dropLabel(buyerBubble);
    dropLabel(sellerBubble);
    buyerBubble = sellerBubble = null;
    flight.phase = "idle";
    packet.visible = false;
    seller.thinkUntil = 0;
  }

  // Snap every running animation to its end state (used when skipping ahead).
  function finish() {
    const t = clock.getElapsedTime();
    if (flight.phase === "out") flight.phase = "wait";
    if (flight.phase === "back") {
      flight.phase = "idle";
      packet.visible = false;
    }
    if (flight.phase === "wait") packet.position.copy(toSeller);
    for (const p of pops) p.mesh.scale.setScalar(1);
    pops.length = 0;
    for (const c of crosses) scene.remove(c.sprite);
    crosses.length = 0;
    for (const ch of [buyer, seller]) {
      ch.talkUntil = Math.min(ch.talkUntil, t);
      ch.refuseUntil = Math.min(ch.refuseUntil, t);
    }
  }

  function setActive(on) {
    if (on === running) return;
    running = on;
    if (on) {
      resize();
      frame();
    } else {
      cancelAnimationFrame(frameHandle);
    }
  }

  return { onEvent, clear, finish, setActive, resize };
}
