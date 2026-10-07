"""Archive-to-agent scheduler. No browser or paid provider is started here."""
import os
import time
from .market import Market
from .queue import Queue


class BackgroundScreening:
    def __init__(self, path, *, interval=300):
        self.path, self.interval = path, interval
        self.last_poll = None
        self.failures = 0
        self.paused = False

    def step(self):
        if self.paused or (self.last_poll is not None and time.monotonic() - self.last_poll < self.interval):
            return None
        self.last_poll = time.monotonic()
        market = queue = None
        try:
            market = Market(self.path)
            queue = Queue(self.path)
            result = market.scan(mode='incremental', queue=queue)
            enrichment = market.enqueue_enrichment(queue)
            self.failures = 0
            return dict(agent_scheduler='screened', **result, enrichment_queued=enrichment)
        except Exception as error:
            # Keep collectors and their credit checkpoints alive if this
            # independent scheduler degrades. Never log DB/provider bodies.
            self.failures += 1
            self.paused = self.failures >= 3
            return dict(agent_scheduler='paused' if self.paused else 'retry_later',
                        error_code='archive_screening_failed', error_type=type(error).__name__,
                        attempts=self.failures, collection_checkpoint_retained=True)
        finally:
            if queue is not None:
                queue.close()
            if market is not None:
                market.close()


def connections():
    """Presence checks only; never return credentials or claim calibration."""
    vision = (os.getenv('DEAL_FINDER_PHOTO_IDENTITY_ENABLED') == '1'
              and bool(os.getenv('OPENAI_API_KEY', '').strip())
              and bool(os.getenv('DEAL_FINDER_VISION_MODEL', '').strip()))
    parts = (os.getenv('DEAL_FINDER_PARTS_WEB_ENABLED') == '1'
             and bool(os.getenv('OPENAI_API_KEY', '').strip())
             and bool(os.getenv('DEAL_FINDER_PARTS_MODEL', '').strip()))
    from .technical_web import configured as risk_configured
    return dict(archive_to_market=os.getenv('DEAL_FINDER_AGENT_SCHEDULER_ENABLED') == '1',
                api_key_present=bool(os.getenv('OPENAI_API_KEY','').strip()),
                vision_model_present=bool(os.getenv('DEAL_FINDER_VISION_MODEL','').strip()),
                parts_model_present=bool(os.getenv('DEAL_FINDER_PARTS_MODEL','').strip()),
                risk_model_present=bool(os.getenv('DEAL_FINDER_RISK_MODEL','').strip()),
                photo_web_provider_configured=vision, parts_web_provider_configured=parts,
                technical_risk_web_provider_configured=risk_configured(),
                paid_provider_calls_opt_in=True, parts_labor_price_enabled=False,
                resale_model_calibrated=False, sale_time_model_calibrated=False,
                verified_publication_enabled=False)
