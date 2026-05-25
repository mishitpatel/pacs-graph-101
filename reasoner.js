// reasoner.js — the closed-form rules engine (the deterministic half of GraphRAG)
//
// One job: answer "can this person open this door at this time?"
// by *traversing the graph*. No SQL, no if-trees per door. The schema
// (ontology.md) and the data (graph.json) together encode the policy;
// this file is just the walker.
//
// Decision rule (read aloud):
//   GRANT iff there exists an AccessGroup G such that
//     Person  --member_of-->        G
//     G       --grants_access_to--> Door
//     G       --active_during-->    Schedule S
//     S       covers `atTime`
//
// Anything else is DENY. We also return the *path* used, so the UI can
// highlight the chain that justified the answer.

export function buildIndex(graph) {
  // adjacency by predicate, both directions, so traversal is O(1) lookup
  const out = new Map();   // (from, predicate) -> [to, to, ...]
  const node = new Map();  // id -> node
  for (const n of graph.nodes) node.set(n.id, n);
  for (const e of graph.edges) {
    const k = `${e.from}::${e.predicate}`;
    if (!out.has(k)) out.set(k, []);
    out.get(k).push(e.to);
  }
  const neighbors = (from, predicate) => out.get(`${from}::${predicate}`) || [];
  return { node, neighbors };
}

// Schedule semantics. In a real PACS this is a calendar object with
// holidays, timezones, exceptions. Here we keep it tiny on purpose:
//   sch_247 — always
//   sch_biz — Mon–Fri, 09:00–17:00 local
// `atTime` is a JS Date.
export function scheduleCovers(scheduleId, atTime) {
  if (scheduleId === "sch_247") return true;
  if (scheduleId === "sch_biz") {
    const day = atTime.getDay();           // 0=Sun..6=Sat
    const hour = atTime.getHours();
    return day >= 1 && day <= 5 && hour >= 9 && hour < 17;
  }
  return false; // unknown schedule = deny by default (fail-closed)
}

export function canAccess(graph, personId, doorId, atTime) {
  const { neighbors } = buildIndex(graph);

  // Walk the chain. For every group the person is in, check whether
  // that group grants this door AND its schedule covers `atTime`.
  for (const groupId of neighbors(personId, "member_of")) {
    const doors = neighbors(groupId, "grants_access_to");
    if (!doors.includes(doorId)) continue;

    for (const schedId of neighbors(groupId, "active_during")) {
      if (scheduleCovers(schedId, atTime)) {
        return {
          decision: "grant",
          path: [personId, groupId, doorId, schedId], // nodes on the justifying chain
          edges: [
            { from: personId, to: groupId,  predicate: "member_of" },
            { from: groupId,  to: doorId,   predicate: "grants_access_to" },
            { from: groupId,  to: schedId,  predicate: "active_during" }
          ],
          reason: `via group "${groupId}" on schedule "${schedId}"`
        };
      }
    }
  }

  // Diagnostic deny: tell the operator *why*. This is what turns a graph
  // from a data structure into an explainable policy engine.
  const groups = neighbors(personId, "member_of");
  if (groups.length === 0) {
    return { decision: "deny", path: [], edges: [], reason: "person is in no AccessGroup" };
  }
  const granting = groups.filter(g => neighbors(g, "grants_access_to").includes(doorId));
  if (granting.length === 0) {
    return { decision: "deny", path: [], edges: [], reason: "no group of this person grants this door" };
  }
  return {
    decision: "deny",
    path: [],
    edges: [],
    reason: `group(s) ${granting.join(", ")} grant the door, but no schedule covers this time`
  };
}
