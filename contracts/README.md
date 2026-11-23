# Foundation contracts

The executable [offline campaign contract](campaign.md) comes with strict standard-library validation and [working examples](../fixtures/campaigns/pass.json). The campaign and case formats each record the provenance applicable to their own inputs.

[case-result.schema.json](case-result.schema.json) fixes only the minimal version-1 case-result envelope: case identity, five acceptance statuses, mode, input/oracle provenance, evidence references, seven evaluation dimensions and overall assessment.

It uses JSON Schema draft 2020-12 with a deliberately small vocabulary. The schema defines a versioned case envelope. Changes to envelope meaning require a version change. Detailed event and Review schemas are deferred until requirements are verified.

Each dimension includes a status and reason. Evidence references use an identifier, result-relative path and SHA-256 digest. Provenance includes fixture and Product SHA-256 digests and an oracle revision. The schema cannot enforce path containment, digest correspondence, evidence relevance, requiredness or semantic accuracy; independent oracles must do so. See [results](../docs/results.md).
