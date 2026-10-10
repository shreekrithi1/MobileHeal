// MobileHeal Design Sync — applies the design approved in MobileHeal to this Figma file.
// 1. Variables: collection "MobileHeal" (ui/<key> COLOR|STRING, fields/<name>/required BOOLEAN).
// 2. Frames: the selected frame(s) or every frame named "Profile": button fill + label, banner fill,
//    required markers (" *") on "Input / <Field>" labels.
figma.showUI(__html__, { width: 320, height: 330 });

function hexToRgb(hex) {
  const h = hex.replace("#", "");
  return { r: parseInt(h.slice(0, 2), 16) / 255, g: parseInt(h.slice(2, 4), 16) / 255, b: parseInt(h.slice(4, 6), 16) / 255 };
}

async function applyVariables(items) {
  const cols = await figma.variables.getLocalVariableCollectionsAsync();
  let col = cols.find((c) => c.name === "MobileHeal") || figma.variables.createVariableCollection("MobileHeal");
  const mode = col.modes[0].modeId;
  const existing = {};
  for (const id of col.variableIds) {
    const v = await figma.variables.getVariableByIdAsync(id);
    if (v) existing[v.name] = v;
  }
  for (const it of items) {
    let v = existing[it.name];
    if (v && v.resolvedType !== it.type) { v.remove(); v = null; }
    if (!v) v = figma.variables.createVariable(it.name, col, it.type);
    v.setValueForMode(mode, it.value);
  }
  return items.length;
}

async function setText(node, text) {
  if (node.type !== "TEXT" || node.characters === text) return false;
  const fonts = node.getRangeAllFontNames(0, node.characters.length);
  for (const f of fonts) await figma.loadFontAsync(f);
  node.characters = text;
  return true;
}

// No "Profile" frame yet → draw the screen from the approved design so it is visible in Figma.
async function createProfileFrame(tokens) {
  await figma.loadFontAsync({ family: "Inter", style: "Regular" });
  await figma.loadFontAsync({ family: "Inter", style: "Bold" });
  const text = (chars, size, bold, color) => {
    const t = figma.createText();
    t.fontName = { family: "Inter", style: bold ? "Bold" : "Regular" };
    t.fontSize = size; t.characters = chars;
    t.fills = [{ type: "SOLID", color: hexToRgb(color || "#101828") }];
    return t;
  };
  const vstack = (name, gap) => {
    const f = figma.createFrame(); f.name = name; f.layoutMode = "VERTICAL"; f.itemSpacing = gap;
    f.primaryAxisSizingMode = "AUTO"; f.counterAxisSizingMode = "FIXED"; f.fills = []; return f;
  };
  const frame = vstack("Profile", 16);
  frame.resize(390, 100); frame.primaryAxisSizingMode = "AUTO"; frame.paddingTop = frame.paddingBottom = 32; frame.paddingLeft = frame.paddingRight = 24;
  frame.fills = [{ type: "SOLID", color: { r: 1, g: 1, b: 1 } }];
  frame.appendChild(text(tokens["ui.app_title"] || "Your profile", 24, true));
  const banner = figma.createFrame(); banner.name = "Banner"; banner.layoutMode = "HORIZONTAL";
  banner.paddingTop = banner.paddingBottom = 12; banner.paddingLeft = banner.paddingRight = 16; banner.cornerRadius = 10;
  banner.primaryAxisSizingMode = "FIXED"; banner.counterAxisSizingMode = "AUTO"; banner.resize(342, 40);
  banner.fills = [{ type: "SOLID", color: hexToRgb(tokens["ui.banner_color"] || "#ECFDF3") }];
  banner.appendChild(text(tokens["ui.banner_message"] || "Changes saved successfully", 14, false));
  frame.appendChild(banner);
  const fields = Object.keys(tokens).filter((k) => !k.startsWith("ui.") && /^(required|optional)$/.test(tokens[k]));
  for (const f of fields) {
    const box = vstack("Input / " + f, 6); box.resize(342, 10); box.primaryAxisSizingMode = "AUTO";
    const label = f.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase()) + (tokens[f] === "required" ? " *" : "");
    box.appendChild(text(label, 13, true, "#344054"));
    const field = figma.createFrame(); field.name = "field"; field.resize(342, 44); field.cornerRadius = 8;
    field.fills = [{ type: "SOLID", color: { r: 1, g: 1, b: 1 } }];
    field.strokes = [{ type: "SOLID", color: hexToRgb("#D0D5DD") }];
    box.appendChild(field); field.layoutSizingHorizontal = "FILL"; frame.appendChild(box);
  }
  const btn = figma.createFrame(); btn.name = "Button / Primary"; btn.layoutMode = "HORIZONTAL";
  btn.primaryAxisAlignItems = "CENTER"; btn.counterAxisAlignItems = "CENTER"; btn.resize(342, 48); btn.cornerRadius = 12;
  btn.primaryAxisSizingMode = "FIXED"; btn.counterAxisSizingMode = "FIXED";
  btn.fills = [{ type: "SOLID", color: hexToRgb(tokens["ui.button_color"] || "#079455") }];
  btn.appendChild(text(tokens["ui.button_label"] || "Save", 16, true, "#FFFFFF"));
  frame.appendChild(btn);
  figma.currentPage.appendChild(frame);
  figma.viewport.scrollAndZoomIntoView([frame]);
  return frame;
}

async function applyFrames(tokens) {
  let frames = figma.currentPage.selection.filter((n) => "findAll" in n);
  if (!frames.length) frames = await profileFrames();
  if (!frames.length) {
    await createProfileFrame(tokens);
    return { frames: 1, changed: 1, created: true };
  }
  // Fields added or removed (e.g. City became required, or was dropped): redraw the frame in place.
  const want = Object.keys(tokens).filter((k) => !k.startsWith("ui.") && /^(required|optional)$/.test(tokens[k])).sort().join(",");
  let redrawn = 0;
  for (const frame of frames) {
    const have = frame.findAll((n) => /^input \/ /i.test(n.name || "")).map((n) => n.name.replace(/^input \/ /i, "").trim().toLowerCase()).sort().join(",");
    if (have !== want && frame.type === "FRAME") {
      const x = frame.x, y = frame.y;
      frame.remove();
      const f = await createProfileFrame(tokens);
      f.x = x; f.y = y;
      redrawn++;
    }
  }
  if (redrawn) return { frames: redrawn, changed: redrawn, redrawn: true };
  let changed = 0;
  for (const frame of frames) {
    for (const node of frame.findAll((n) => "name" in n)) {
      const name = node.name.toLowerCase();
      if (/\b(button|btn|cta|primary)\b/.test(name) && "fills" in node) {
        if (tokens["ui.button_color"]) { node.fills = [{ type: "SOLID", color: hexToRgb(tokens["ui.button_color"]) }]; changed++; }
        const label = node.findOne && node.findOne((n) => n.type === "TEXT");
        if (label && tokens["ui.button_label"] && (await setText(label, tokens["ui.button_label"]))) changed++;
      } else if (/\b(banner|alert|callout)\b/.test(name) && "fills" in node && tokens["ui.banner_color"]) {
        node.fills = [{ type: "SOLID", color: hexToRgb(tokens["ui.banner_color"]) }]; changed++;
      } else if (/\b(input|field|text ?field)\b/.test(name) && node.findOne) {
        const label = node.findOne((n) => n.type === "TEXT");
        if (!label) continue;
        const field = label.characters.replace(/\*/g, "").trim().toLowerCase().replace(/[^a-z0-9]+/g, "_");
        const rule = tokens[field] || tokens[{ phone: "phone_number", mobile: "phone_number", full_name: "name" }[field]];
        if (!rule) continue;
        const want = label.characters.replace(/\s*\*$/, "") + (rule === "required" ? " *" : "");
        if (await setText(label, want)) changed++;
      }
    }
  }
  return { frames: frames.length, changed };
}

// ---- Figma → MobileHeal: read the design tokens back out of the frame(s)
function rgbToHex(c) {
  const h = (x) => Math.round(x * 255).toString(16).padStart(2, "0").toUpperCase();
  return "#" + h(c.r) + h(c.g) + h(c.b);
}
async function profileFrames() {
  await figma.loadAllPagesAsync();
  return figma.root.findAll((n) => n.type === "FRAME" && /profile/i.test(n.name));
}
async function readTokens() {
  const tokens = {};
  for (const frame of await profileFrames()) {
    for (const node of frame.findAll((n) => "name" in n)) {
      const name = node.name.toLowerCase();
      const fill = "fills" in node && Array.isArray(node.fills) && node.fills.find((f) => f.type === "SOLID");
      if (/\b(button|btn|cta|primary)\b/.test(name)) {
        if (fill) tokens["ui.button_color"] = rgbToHex(fill.color);
        const label = node.findOne && node.findOne((n) => n.type === "TEXT");
        if (label) tokens["ui.button_label"] = label.characters.trim();
      } else if (/\b(banner|alert|callout)\b/.test(name) && fill) {
        tokens["ui.banner_color"] = rgbToHex(fill.color);
      } else if (/\b(input|field|text ?field)\b/.test(name) && node.findOne) {
        const label = node.findOne((n) => n.type === "TEXT");
        if (!label) continue;
        let field = label.characters.replace(/\*/g, "").trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "");
        field = { phone: "phone_number", mobile: "phone_number", full_name: "name" }[field] || field;
        if (field) tokens[field] = /\*\s*$/.test(label.characters) ? "required" : "optional";
      }
    }
  }
  return tokens;
}
let applying = false, timer = null;
figma.loadAllPagesAsync().then(() => figma.on("documentchange", () => {
  if (applying) return;                       // our own writes are not designer edits
  clearTimeout(timer);
  timer = setTimeout(async () => {
    figma.ui.postMessage({ type: "edited", tokens: await readTokens(), user: (figma.currentUser || {}).name || "a designer", file: figma.root.name });
  }, 2500);
}));

figma.ui.onmessage = async (msg) => {
  if (msg.type === "read") {
    figma.ui.postMessage({ type: "edited", tokens: await readTokens(), user: (figma.currentUser || {}).name || "a designer", file: figma.root.name, manual: true });
    return;
  }
  if (msg.type !== "apply") return;
  applying = true;
  try {
    let text;
    try { text = `Updated ${await applyVariables(msg.data.variables)} variables in “MobileHeal”.`; }
    catch (e) { text = "Variables skipped (" + e.message + ")."; }
    if (!msg.onlyVars) {
      const r = await applyFrames(msg.data.tokens);
      text += r.created ? " Drew a new “Profile” frame from the approved design." : r.redrawn ? " Fields changed — redrew the “Profile” frame." : ` ${r.changed} layer change(s) across ${r.frames} frame(s).`;
    }
    figma.notify("MobileHeal design applied");
    figma.ui.postMessage({ text: text });
  } catch (e) {
    figma.ui.postMessage({ text: "Failed: " + e.message, error: true });
  }
  setTimeout(() => { applying = false; }, 3000);
};
