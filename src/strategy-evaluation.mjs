import { sign, verify } from "node:crypto";
import {
  EMPTY_WITNESS_ROOT,
  identifyJsonSemanticContent,
} from "@red-cup-engineering/computer-code-engine/semiotic-expression-notation/semantic-content-identity";

// Signed-record convention: the subject id is the typed v2 semantic identity
// of the admitted JSON record excluding only top-level id and signature.
// Ed25519 signs that exact ASCII subject id; signature is unpadded base64url.
export function strategyRecordId(body) {
  const value = structuredClone(body);
  delete value.id;
  delete value.signature;
  return identifyJsonSemanticContent({
    objectKind: "brokerage.strategy-evaluation.record",
    value,
    witnessRoot: EMPTY_WITNESS_ROOT,
  }).id;
}
export function deepFreeze(value, seen = new Set()) { if (value === null || typeof value !== "object") return value; if (seen.has(value)) throw new TypeError("cyclic-value-refused"); seen.add(value); for (const child of Object.values(value)) deepFreeze(child, seen); seen.delete(value); return Object.freeze(value); }
export function identify(body) { const copy = structuredClone(body); delete copy.id; delete copy.signature; return deepFreeze({ id: strategyRecordId(copy), ...copy }); }
export function signRecord(body, privateKey) { const subject = identify(body); return deepFreeze({ ...subject, signature: sign(null, Buffer.from(subject.id), privateKey).toString("base64url") }); }
export function verifyRecord(record, type, label = type) { if (!record || record.type !== type || record.id !== strategyRecordId(record) || "signature" in record) throw new TypeError(`${label}-content-id-invalid`); return deepFreeze(structuredClone(record)); }
export function freezeTrustedActorKeys(keys) { if (!keys || typeof keys !== "object") throw new TypeError("trusted-actor-keys-required"); const copy = Object.create(null); for (const [actor, key] of Object.entries(keys)) { if (!actor || !key) throw new TypeError("trusted-actor-key-invalid"); copy[actor] = key; } return Object.freeze(copy); }
export function verifySignedRecord(record, type, actorField, trustedActorKeys, label = type) {
  if (!record || record.type !== type || record.id !== strategyRecordId(record) || typeof record.signature !== "string") throw new TypeError(`${label}-signed-content-invalid`);
  if (!/^[A-Za-z0-9_-]{86}$/u.test(record.signature)) throw new TypeError(`${label}-signature-encoding-invalid`);
  const signatureBytes = Buffer.from(record.signature, "base64url");
  if (signatureBytes.length !== 64 || signatureBytes.toString("base64url") !== record.signature) throw new TypeError(`${label}-signature-encoding-invalid`);
  const actor = record[actorField], key = trustedActorKeys?.[actor]; if (!actor || !key || !verify(null, Buffer.from(record.id), key, signatureBytes)) throw new TypeError(`${label}-signature-invalid`);
  return deepFreeze(structuredClone(record));
}
const requireSha256NiUri = (v, label) => { if (typeof v !== "string" || !/^ni:\/\/\/sha-256;[A-Za-z0-9_-]{43}$/u.test(v)) throw new TypeError(`${label}-digest-invalid`); return v; };
const finite = (v, label, nonnegative = false) => { const n = Number(v); if (!Number.isFinite(n) || Object.is(n, -0) || (nonnegative && n < 0)) throw new TypeError(`${label}-domain-invalid`); return n; };
export function validateStrategyCandidate(record, { contextProduct, incumbent } = {}) {
  const c = verifyRecord(record, "ImmutableStrategyCandidate", "strategy-candidate"); requireSha256NiUri(c.contextProduct, "candidate-context"); requireSha256NiUri(c.basedOn, "candidate-incumbent");
  if (contextProduct && (c.contextProduct !== contextProduct.id || !Array.isArray(contextProduct.sourceDigests))) throw new TypeError("candidate-context-binding-invalid"); if (incumbent && c.basedOn !== incumbent.id) throw new TypeError("candidate-incumbent-binding-invalid");
  if (!Array.isArray(c.sourceEvidence) || !c.sourceEvidence.length || c.sourceEvidence.some((e) => !Number.isFinite(Date.parse(e?.observedAt)) || !/^ni:\/\/\/sha-256;[A-Za-z0-9_-]{43}$/u.test(e?.digest) || (contextProduct && !contextProduct.sourceDigests.includes(e.digest)))) throw new TypeError("candidate-source-evidence-invalid");
  if (!Array.isArray(c.assumptions) || !c.assumptions.length || c.assumptions.some((x) => typeof x !== "string" || !x.trim()) || !c.falsifier || !["gte", "lte"].includes(c.falsifier.operator) || typeof c.falsifier.metric !== "string") throw new TypeError("candidate-analysis-invalid"); finite(c.falsifier.threshold, "falsifier-threshold"); return c;
}
export function validateReplayDataset(record, trustedActorKeys) { const d = verifySignedRecord(record, "ContentAddressedReplayDataset", "dataProviderActor", trustedActorKeys, "replay-dataset"); requireSha256NiUri(d.contentDigest, "replay-content"); requireSha256NiUri(d.provenance, "replay-provenance"); return d; }
export function validateReplayDatasetAssay(record, dataset, trustedActorKeys) { const a = verifySignedRecord(record, "ReplayDatasetAssay", "assayerActor", trustedActorKeys, "replay-dataset-assay"); if (a.dataset !== dataset.id || a.contentDigest !== dataset.contentDigest || a.assayerActor === dataset.dataProviderActor || a.contentAvailable !== true || typeof a.withheldMethod !== "string" || !a.withheldMethod.trim() || a.withheldFromComputeProvider !== true) throw new TypeError("replay-dataset-assay-invalid"); return a; }
function executorResult(record, { candidate, dataset, trustedActorKeys, executorActor, excludedActors }) {
  const r = verifySignedRecord(record, "BoundedReplayExecutorResult", "executorActor", trustedActorKeys, "replay-result"); if (r.executorActor !== executorActor || excludedActors.includes(r.executorActor) || r.candidate !== candidate.id || r.replayDataset !== dataset.id || !r.executorVersion) throw new TypeError("replay-result-binding-invalid"); requireSha256NiUri(r.implementationDigest, "executor-implementation");
  if (!Array.isArray(r.trades) || !r.trades.length) throw new TypeError("replay-trades-required"); return { record: r, trades: r.trades.map((t) => ({ grossPnl: finite(t.grossPnl, "trade-gross"), cost: finite(t.cost, "trade-cost", true), slippage: finite(t.slippage, "trade-slippage", true) })) };
}
function metrics(trades) { let grossPnl = 0, cost = 0, slippage = 0, equity = 0, peak = 0, maximumDrawdown = 0; for (const t of trades) { grossPnl += t.grossPnl; cost += t.cost; slippage += t.slippage; equity += t.grossPnl - t.cost - t.slippage; peak = Math.max(peak, equity); maximumDrawdown = Math.max(maximumDrawdown, peak - equity); } const netPnl = grossPnl - cost - slippage; if (![grossPnl, cost, slippage, netPnl, maximumDrawdown].every(Number.isFinite)) throw new TypeError("replay-metrics-invalid"); return { grossPnl, cost, slippage, netPnl, maximumDrawdown, sampleSize: trades.length }; }
export async function evaluateStrategyCandidate({ candidate, contextProduct, replayDataset, replayDatasetAssay, replayExecutor, minimumSamples, trustedActorKeys, executorActor, excludedActors = [] }) {
  candidate = validateStrategyCandidate(candidate, { contextProduct }); replayDataset = validateReplayDataset(replayDataset, trustedActorKeys); validateReplayDatasetAssay(replayDatasetAssay, replayDataset, trustedActorKeys); if (typeof replayExecutor !== "function" || !Number.isInteger(minimumSamples) || minimumSamples < 1) throw new TypeError("replay-contract-invalid");
  const input = deepFreeze({ candidate, replayDataset }); const first = executorResult(await replayExecutor(input), { candidate, dataset: replayDataset, trustedActorKeys, executorActor, excludedActors }); const second = executorResult(await replayExecutor(input), { candidate, dataset: replayDataset, trustedActorKeys, executorActor, excludedActors }); if (first.record.id !== second.record.id) throw new TypeError("nondeterministic-replay-refused");
  const m = metrics(first.trades); if (m.sampleSize < minimumSamples) throw new RangeError("replay-sample-insufficient"); if (!(candidate.falsifier.metric in m)) throw new TypeError("falsifier-metric-unsupported"); const observed = m[candidate.falsifier.metric], triggered = candidate.falsifier.operator === "gte" ? observed >= candidate.falsifier.threshold : observed <= candidate.falsifier.threshold;
  return identify({ type: "ReplayableStrategyEvaluation", version: 3, candidate: candidate.id, replayDataset: replayDataset.id, replayDatasetAssay: replayDatasetAssay.id, executorResult: first.record.id, metrics: m, falsifierOutcome: { ...candidate.falsifier, observed, triggered }, alphaEstablished: false, profitabilityEstablished: false, liveReadinessEstablished: false });
}
