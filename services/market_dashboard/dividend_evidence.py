"""Reconcile dated dividend observations without turning announcements into cash."""
import re
from copy import copy, deepcopy
from datetime import date

from .analysis import numeric
from .financial_dates import publication_date


def with_field_availability(event):
    """Missing date evidence does not hide an independently reported dividend.

    Statuses describe source observations, not universal event verification.
    In particular a past scheduled payment remains announced, not confirmed.
    """
    if event.get('kind') != 'dividend':
        return event
    result = copy(event)
    conflicts = set(event.get('evidence_conflicts') or [])
    dates = {}
    for field in ('record_date', 'ex_date', 'payment_date'):
        try:
            dates[field] = date.fromisoformat(event.get(field))
        except (TypeError, ValueError):
            dates[field] = None
    fields = {}
    for field in ('amount', 'record_date', 'ex_date', 'payment_date'):
        value = event.get(field)
        status = 'reported' if value is not None and value != '' else 'missing'
        if field == 'amount' and numeric(value) is None:
            status = 'missing'
        if field.endswith('_date') and status != 'missing':
            day, record = dates[field], dates['record_date']
            if day is None:
                status = 'invalid'
            elif record is not None and (
                    (field == 'ex_date' and day > record)
                    or (field == 'payment_date' and day < record)):
                status = 'invalid'
        if field == 'payment_date' and status == 'reported':
            status = 'confirmed' if event.get('payment_status') == 'confirmed' else 'announced'
        if (field in conflicts
                or (field == 'payment_date' and 'confirmed_payment_date' in conflicts)):
            status = 'conflicting'
        if event.get('superseded_by'):
            status = 'superseded'
        fields[field] = status
    result['field_availability'] = fields
    return result


def enrich_dividends(events, observations):
    result = deepcopy(events)
    for event in result:
        if event.get('kind') != 'dividend' or event.get('superseded_by'):
            continue
        matched = []
        for observation in observations:
            if any(observation.get(k) != event.get(k) for k in
                   ('security_code', 'share_class', 'record_date', 'currency')):
                continue
            if numeric(observation.get('amount')) is None or numeric(observation['amount']) != numeric(event.get('amount')):
                continue
            trace = observation.get('source_trace') or {}
            if (not observation.get('id') or observation.get('reviewed') is not True
                    or not trace.get('source_url') or not trace.get('source')
                    or not re.fullmatch('[0-9a-f]{64}', str(trace.get('sha256', '')))):
                continue
            published, _ = publication_date({'published_at': trace.get('available_at')})
            for field in ('ex_date', 'confirmed_payment_date'):
                value = observation.get(field)
                if not value:
                    continue
                try:
                    date.fromisoformat(value)
                    record = date.fromisoformat(event['record_date'])
                except (TypeError, ValueError):
                    continue
                if field == 'ex_date' and date.fromisoformat(value) > record:
                    continue
                if field == 'confirmed_payment_date' and (
                        date.fromisoformat(value) < record or not published or value > published[:10]):
                    continue
                matched.append((field, value, observation['id'], trace))
        # A partial reprocessing pass is not a resolution of previously found
        # contradictory evidence. Source corrections must resolve the event
        # explicitly before this ledger can become verified again.
        conflicts = list(event.get('evidence_conflicts') or [])
        for field, target in [('ex_date', 'ex_date'), ('confirmed_payment_date', 'payment_date')]:
            candidates = [m for m in matched if m[0] == field]
            values = {m[1] for m in candidates}
            # A previously confirmed value remains evidence; a scheduled date is
            # not a competing confirmation when the actual payment was delayed.
            if event.get(target) and (field == 'ex_date' or event.get('payment_status') == 'confirmed'):
                values.add(event[target])
            if len(values) > 1:
                if field not in conflicts:
                    conflicts.append(field)
            elif candidates:
                event[target] = candidates[0][1]
                if field == 'confirmed_payment_date':
                    event['payment_status'] = 'confirmed'
                event.setdefault('field_evidence', {})[target] = [
                    {'observation_id': m[2], 'source_trace': m[3]} for m in candidates]
        if conflicts:
            event.update(verified=False, status='conflicting_dividend_evidence', evidence_conflicts=conflicts)
        else:
            try:
                record = date.fromisoformat(event['record_date'])
                ex_day = date.fromisoformat(event['ex_date']) if event.get('ex_date') else None
                payment = date.fromisoformat(event['payment_date']) if event.get('payment_date') else None
                valid_dates = (ex_day is None or ex_day <= record) and (payment is None or payment >= record)
            except (KeyError, TypeError, ValueError):
                valid_dates = False
                ex_day = payment = None
            if not valid_dates:
                event.update(verified=False, status='dividend_dates_require_review')
                continue
            verified = bool(ex_day and payment and event.get('payment_status') == 'confirmed')
            event.update(verified=verified, status='verified' if verified else
                         'ex_date_required' if not event.get('ex_date') else 'payment_confirmation_required')
        if event.get('payment_date'):
            event.update(date=event['payment_date'], date_kind='payment')
    return [with_field_availability(event) for event in result]
