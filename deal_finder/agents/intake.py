"""Explicit ingestion agent; normalization and queue creation share one transaction."""
class IntakeAgent:
    name = 'intake'
    purpose = 'Preserve raw batches, quarantine invalid records and enqueue durable analysis jobs.'
    def execute(self, queue, batch_id, records):
        return queue.submit(batch_id, records)
