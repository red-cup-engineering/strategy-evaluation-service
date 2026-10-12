import assert from "node:assert/strict";
import { generateKeyPairSync } from "node:crypto";
import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { createContentAddressedDatasetStore } from "@red-cup-engineering/content-addressed-dataset-store-service";
import { evaluateStrategyCandidate, identify, signRecord } from "../src/strategy-evaluation.mjs";

const digest = (character) => `ni:///sha-256;${character.repeat(43)}`;

test("a stored dataset digest survives signed replay evaluation", async () => {
  const store = createContentAddressedDatasetStore({ root: await mkdtemp(join(tmpdir(), "strategy-dataset-seam-")) });
  const manifest = await store.storeDataset({
    dataset: { trades: [{ grossPnl: 2, cost: 0.1, slippage: 0.1 }] },
    observedAt: "2099-01-01T00:00:00.000Z", provenance: [digest("P")],
  });
  const actors = Object.fromEntries(["data", "assayer", "executor"].map((name) => {
    const pair = generateKeyPairSync("ed25519");
    return [name, { actor: `urn:actor:${name}`, ...pair }];
  }));
  const trustedActorKeys = Object.fromEntries(Object.values(actors).map(({ actor, publicKey }) => [actor, publicKey]));
  const contextProduct = { id: digest("C"), sourceDigests: [digest("S")] };
  const candidate = identify({
    type: "ImmutableStrategyCandidate", version: 1, contextProduct: contextProduct.id, basedOn: digest("I"),
    sourceEvidence: [{ observedAt: "2099-01-01T00:00:00.000Z", digest: digest("S") }],
    assumptions: ["The bounded replay carrier is representative."],
    falsifier: { metric: "netPnl", operator: "lte", threshold: 0 },
  });
  const dataset = signRecord({
    type: "ContentAddressedReplayDataset", version: 1, dataProviderActor: actors.data.actor,
    contentDigest: manifest.id, provenance: digest("P"),
  }, actors.data.privateKey);
  const assay = signRecord({
    type: "ReplayDatasetAssay", version: 1, assayerActor: actors.assayer.actor,
    dataset: dataset.id, contentDigest: manifest.id, contentAvailable: true,
    withheldMethod: "content-addressed local replay", withheldFromComputeProvider: true,
  }, actors.assayer.privateKey);
  const replayExecutor = async ({ candidate: admittedCandidate, replayDataset }) => signRecord({
    type: "BoundedReplayExecutorResult", version: 1, executorActor: actors.executor.actor,
    candidate: admittedCandidate.id, replayDataset: replayDataset.id, executorVersion: "test:1",
    implementationDigest: digest("E"), trades: [{ grossPnl: 2, cost: 0.1, slippage: 0.1 }],
  }, actors.executor.privateKey);
  const evaluation = await evaluateStrategyCandidate({
    candidate, contextProduct, replayDataset: dataset, replayDatasetAssay: assay,
    replayExecutor, minimumSamples: 1, trustedActorKeys, executorActor: actors.executor.actor,
  });
  assert.deepEqual(await store.readDataset(manifest), { trades: [{ grossPnl: 2, cost: 0.1, slippage: 0.1 }] });
  assert.equal(dataset.contentDigest, manifest.id);
  assert.equal(evaluation.replayDataset, dataset.id);
});
