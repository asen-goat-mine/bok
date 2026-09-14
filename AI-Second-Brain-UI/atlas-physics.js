"use strict";

// DOM-free graph math, shared by the browser adapter and deterministic tests.
(function (root) {
  function worldToScreen(point, camera, width, height) {
    return {
      x: width / 2 + camera.x + (point.x - width / 2) * camera.scale,
      y: height / 2 + camera.y + (point.y - height / 2) * camera.scale,
    };
  }

  function screenToWorld(point, camera, width, height) {
    return {
      x: width / 2 + (point.x - width / 2 - camera.x) / camera.scale,
      y: height / 2 + (point.y - height / 2 - camera.y) / camera.scale,
    };
  }

  function step(graph, width, height) {
    const { alpha, nodes, edges, groups } = graph;
    if (alpha <= 0) return { alpha, movement: 0 };
    let movement = 0;
    nodes.forEach((node) => {
      const anchor = groups[node.group] || { x: width / 2, y: height / 2 };
      node.vx += (anchor.x - node.x) * 0.0026 * alpha;
      node.vy += (anchor.y - node.y) * 0.0026 * alpha;
      node.vx += (width / 2 - node.x) * 0.00034 * alpha;
      node.vy += (height / 2 - node.y) * 0.00034 * alpha;
    });
    const repel = (first, second) => {
      const dx = second.x - first.x;
      const dy = second.y - first.y;
      const distanceSquared = Math.max(49, dx * dx + dy * dy);
      if (distanceSquared > 15000) return;
      const distance = Math.sqrt(distanceSquared);
      const force = 650 * alpha / distanceSquared;
      const forceX = dx / distance * force;
      const forceY = dy / distance * force;
      first.vx -= forceX; first.vy -= forceY;
      second.vx += forceX; second.vy += forceY;
    };
    if (nodes.length < 160) {
      for (let firstIndex = 0; firstIndex < nodes.length; firstIndex += 1) {
        for (let secondIndex = firstIndex + 1; secondIndex < nodes.length; secondIndex += 1) repel(nodes[firstIndex], nodes[secondIndex]);
      }
    } else {
      // Repulsion already has a fixed distance cutoff. Nearby cells retain exactly
      // those interactions without visiting every pair in a large Vault.
      const cellSize = Math.sqrt(15000);
      const cells = new Map();
      nodes.forEach((node, index) => {
        const key = `${Math.floor(node.x / cellSize)}:${Math.floor(node.y / cellSize)}`;
        if (!cells.has(key)) cells.set(key, []);
        cells.get(key).push(index);
      });
      nodes.forEach((first, firstIndex) => {
        const cellX = Math.floor(first.x / cellSize); const cellY = Math.floor(first.y / cellSize);
        for (let x = cellX - 1; x <= cellX + 1; x += 1) {
          for (let y = cellY - 1; y <= cellY + 1; y += 1) {
            for (const secondIndex of cells.get(`${x}:${y}`) || []) {
              if (secondIndex > firstIndex) repel(first, nodes[secondIndex]);
            }
          }
        }
      });
    }
    edges.forEach((edge) => {
      const first = nodes[edge.a];
      const second = nodes[edge.b];
      const dx = second.x - first.x;
      const dy = second.y - first.y;
      const distance = Math.max(1, Math.hypot(dx, dy));
      const desired = edge.kind === "reference" ? 58 : 74;
      const force = (distance - desired) * (edge.kind === "reference" ? 0.004 : 0.0025) * alpha;
      const forceX = dx / distance * force;
      const forceY = dy / distance * force;
      first.vx += forceX; first.vy += forceY;
      second.vx -= forceX; second.vy -= forceY;
    });
    nodes.forEach((node) => {
      node.vx *= 0.84; node.vy *= 0.84;
      const speed = Math.hypot(node.vx, node.vy);
      if (speed > 3.4) { node.vx *= 3.4 / speed; node.vy *= 3.4 / speed; }
      node.x = Math.max(22, Math.min(width - 22, node.x + node.vx));
      node.y = Math.max(52, Math.min(height - 22, node.y + node.vy));
      movement += Math.hypot(node.vx, node.vy);
    });
    return { alpha: Math.max(0.012, alpha * 0.982), movement: nodes.length ? movement / nodes.length : 0 };
  }

  root.BokAtlasPhysics = Object.freeze({ step, worldToScreen, screenToWorld });
})(globalThis);
