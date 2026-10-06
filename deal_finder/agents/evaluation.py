"""Evaluate supplied, frozen holdout predictions against attested sale outcomes.
This measures a model; it does not train one or certify supplied evidence.
"""
from math import ceil
from statistics import mean, median
from .contracts import Result, instant, evidence, cents

class EvaluationAgent:
    name = 'evaluation'
    purpose = 'Measure holdout errors and interval coverage on distinct verified vehicle sales.'

    def execute(self, records, *, model_version, training_vehicle_ids, as_of, minimum=30):
        if type(minimum) is not int or minimum < 30:
            raise ValueError('At least 30 distinct holdout vehicles required')
        if not isinstance(records, list) or not isinstance(training_vehicle_ids, (list, set)):
            raise ValueError('Evaluation requires records and training vehicle identities')
        if not isinstance(model_version, str) or not model_version.strip():
            raise ValueError('Nonempty model version required')
        if any(not isinstance(x, str) or not x.strip() for x in training_vehicle_ids):
            raise ValueError('Training identities must be nonempty strings')
        seen, accepted, rejected = set(), [], []
        training = {x.strip() for x in training_vehicle_ids}
        for index, row in enumerate(records):
            try:
                vehicle = row['vehicle_id']
                if isinstance(vehicle, str):
                    vehicle = vehicle.strip()
                if not isinstance(vehicle, str) or not vehicle.strip() or vehicle in training or vehicle in seen:
                    raise ValueError('Vehicle missing, duplicated or present in training data')
                if row['model_version'] != model_version:
                    raise ValueError('Prediction belongs to a different model version')
                if row.get('verified') is not True or not isinstance(row.get('verified_by'), str) or not row['verified_by'].strip() or row.get('data_kind') != 'observed_transaction':
                    raise ValueError('Sale requires attested observed transaction evidence')
                evidence(row['evidence_url'])
                cutoff = instant(row['training_cutoff'])
                prediction_at, sold_at = instant(row['predicted_at']), instant(row['sold_at'])
                if not cutoff <= prediction_at < sold_at <= as_of:
                    raise ValueError('Temporal leakage or future transaction')
                predicted, sold = cents(row['predicted_cents']), cents(row['sold_cents'])
                if predicted == 0 or sold == 0:
                    raise ValueError('Sale and predicted price must be positive')
                low, high = cents(row['interval_low_cents']), cents(row['interval_high_cents'])
                if low > predicted or high < predicted:
                    raise ValueError('Prediction outside supplied interval')
                segment = row['segment']
                if not isinstance(segment, str) or not segment.strip():
                    raise ValueError('Segment required')
                repair_error = None
                if any(key in row for key in ('predicted_repair_high_cents','actual_repair_cents','repair_evidence_url')):
                    evidence(row['repair_evidence_url'])
                    predicted_repairs=cents(row['predicted_repair_high_cents'])
                    actual_repairs=cents(row['actual_repair_cents'])
                    repair_error=actual_repairs-predicted_repairs
                seen.add(vehicle)
                accepted.append(dict(vehicle_id=vehicle, segment=segment, error=predicted-sold,
                                     percentage=abs(predicted-sold)/sold*100, covered=low<=sold<=high,
                                     repair_cost_underestimate_cents=repair_error))
            except (ValueError, KeyError, TypeError) as error:
                rejected.append({'index': index, 'reason': str(error)})
        def metrics(rows):
            errors = sorted(abs(x['error']) for x in rows)
            overpricing = sorted(max(0,x['error']) for x in rows)
            return dict(count=len(rows), mae_cents=round(mean(errors)),
                        median_absolute_percentage_error=round(median(x['percentage'] for x in rows), 3),
                        p90_absolute_error_cents=errors[ceil(len(rows)*0.9)-1],
                        bias_cents=round(mean(x['error'] for x in rows)),
                        interval_coverage=round(mean(x['covered'] for x in rows), 4),
                        overpricing_fraction=round(mean(x['error']>0 for x in rows),4),
                        p90_overpricing_cents=overpricing[ceil(len(rows)*0.9)-1])
        groups = {}
        for row in accepted:
            groups.setdefault(row['segment'], []).append(row)
        repairs=[x['repair_cost_underestimate_cents'] for x in accepted if x['repair_cost_underestimate_cents'] is not None]
        learning = dict(overpricing_cases=[x['vehicle_id'] for x in accepted if x['error']>0],
                        repair_underestimate_cases=[x['vehicle_id'] for x in accepted
                            if x['repair_cost_underestimate_cents'] is not None and x['repair_cost_underestimate_cents']>0],
                        repair_sample_count=len(repairs),
                        mean_repair_underestimate_cents=round(mean(max(0,x) for x in repairs)) if repairs else None,
                        automatic_policy_changes=False, independent_validation_required=True)
        return Result(self.name, 'completed' if len(accepted)>=minimum and not rejected else 'blocked', {
            'model_version': model_version, 'accepted_count': len(accepted), 'rejected': rejected,
            'metrics': metrics(accepted) if accepted else None,
            'segments': {name: dict(status='sufficient_sample' if len(rows)>=minimum else 'insufficient_sample',
                                   metrics=metrics(rows)) for name, rows in groups.items()},
            'forecast_release_approved': False,
            'learning_review': learning,
            'verification': 'human_attestation_not_independent_certification'},
            ['Metrics require independent evidence review and explicit release thresholds before predictions can be enabled.'])
