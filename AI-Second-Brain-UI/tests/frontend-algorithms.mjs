import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");
const section = (start, end) => source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)));
const context = vm.createContext({ state: {}, Math });
vm.runInContext(readFileSync(new URL("../atlas-physics.js", import.meta.url), "utf8"), context);
vm.runInContext(section("const CONFIG", "const CATEGORY_MAP"), context);
vm.runInContext(section("function normalizePath(", "function readFrontmatter("), context);
vm.runInContext(section("function renderInline(", "const fullRecordLoads"), context);
const physics = context.BokAtlasPhysics;
const camera = { x: 90, y: -25, scale: 1.4 };
const point = { x: 370, y: 220 };
const screen = physics.worldToScreen(point, camera, 1200, 800);
const restored = physics.screenToWorld(screen, camera, 1200, 800);
assert.ok(Math.abs(restored.x - point.x) < 1e-10 && Math.abs(restored.y - point.y) < 1e-10);

const render = (text) => context.markdownToHtml(text, "03-Knowledge/sample.md");
assert.equal(context.isIgnoredPath("Vault/.bok/versions/example/before.md"), true);
assert.equal(context.isIgnoredPath("Vault/03-Knowledge/example.md"), false);
assert.equal(render("# Title\n\n**bold** <script>"), '<h1 id="section-0-Title">Title</h1>\n<p><strong>bold</strong> &lt;script&gt;</p>');
assert.equal(render("---\ntitle: metadata\n---\n# Title"), '<h1 id="section-0-Title">Title</h1>');
assert.match(render("[Read](./next.md)"), /data-md-path="03-Knowledge\/next.md"/u);
assert.match(render("```js\nconst x = '<p>';\n```"), /const x = &#039;&lt;p&gt;&#039;;/u);
assert.match(render("- [x] done\n- [ ] next"), /class="task-list"[\s\S]*is-done[\s\S]*next/u);
assert.match(render("| A | B |\n| --- | --- |\n| one | two |"), /<thead>[\s\S]*<th>A<\/th>[\s\S]*<td>two<\/td>/u);

// Reference pairwise force model: the spatial grid must preserve the existing
// layout physics, including interactions on either side of a cell boundary.
function referenceStep(state, width, height) {
  const alpha = state.atlasSimulationAlpha;
  const nodes = state.atlasNodes;
  for (const node of nodes) {
    const anchor = state.atlasGroups[node.group];
    node.vx += (anchor.x - node.x) * 0.0026 * alpha + (width / 2 - node.x) * 0.00034 * alpha;
    node.vy += (anchor.y - node.y) * 0.0026 * alpha + (height / 2 - node.y) * 0.00034 * alpha;
  }
  for (let i = 0; i < nodes.length; i += 1) for (let j = i + 1; j < nodes.length; j += 1) {
    const first = nodes[i]; const second = nodes[j];
    const dx = second.x - first.x; const dy = second.y - first.y;
    const squared = Math.max(49, dx * dx + dy * dy);
    if (squared > 15000) continue;
    const magnitude = 650 * alpha / squared / Math.sqrt(squared);
    first.vx -= dx * magnitude; first.vy -= dy * magnitude;
    second.vx += dx * magnitude; second.vy += dy * magnitude;
  }
  for (const edge of state.atlasEdges) {
    const first = nodes[edge.a]; const second = nodes[edge.b];
    const dx = second.x - first.x; const dy = second.y - first.y;
    const distance = Math.max(1, Math.hypot(dx, dy));
    const magnitude = (distance - (edge.kind === "reference" ? 58 : 74)) * (edge.kind === "reference" ? 0.004 : 0.0025) * alpha / distance;
    first.vx += dx * magnitude; first.vy += dy * magnitude;
    second.vx -= dx * magnitude; second.vy -= dy * magnitude;
  }
  for (const node of nodes) {
    node.vx *= 0.84; node.vy *= 0.84;
    const speed = Math.hypot(node.vx, node.vy);
    if (speed > 3.4) { node.vx *= 3.4 / speed; node.vy *= 3.4 / speed; }
    node.x = Math.max(22, Math.min(width - 22, node.x + node.vx));
    node.y = Math.max(52, Math.min(height - 22, node.y + node.vy));
  }
  state.atlasSimulationAlpha = Math.max(0.012, alpha * 0.982);
}
for (const count of [40, 600]) {
  const initial = {
    atlasSimulationAlpha: 1,
    atlasGroups: [{ x: 1000, y: 800 }],
    atlasEdges: [{ a: 0, b: 2, kind: "reference" }, { a: 8, b: 15, kind: "tag" }],
    atlasNodes: Array.from({ length: count }, (_, index) => ({ x: (index * 277) % 2200, y: 52 + (index * 439) % 1700, vx: 0, vy: 0, group: 0 })),
  };
  const copy = structuredClone(initial);
  const graph = { nodes: copy.atlasNodes, edges: copy.atlasEdges, groups: copy.atlasGroups, alpha: copy.atlasSimulationAlpha };
  const expected = structuredClone(initial);
  for (let step = 0; step < 12; step += 1) { graph.alpha = physics.step(graph, 2300, 1800).alpha; referenceStep(expected, 2300, 1800); }
  expected.atlasNodes.forEach((node, index) => {
    for (const key of ["x", "y", "vx", "vy"]) assert.ok(Math.abs(node[key] - graph.nodes[index][key]) < 1e-8, `${count} nodes: ${index}.${key} changed`);
  });
}
console.log("Frontend algorithms: Markdown rendering and both graph physics paths passed.");
