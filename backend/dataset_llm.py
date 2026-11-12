"""Inference policy for structured dataset generation and quality review."""
from backend.llm_parameters import describe_parameters, resolve_parameters


def generation_parameters(config, key, max_tokens=8192):
    """Disable optional reasoning only for explicitly supported model UIDs.

    These calls need schema-valid answers at every SDK stage. Provider defaults
    can otherwise spend the entire bounded request on reasoning. Resolve wire
    fields through the shared capability registry; unknown models stay basic.
    """
    specs = describe_parameters(config, key)['parameters']
    thinking = specs.get('thinking_mode', {})
    overrides = {'thinking_mode': 'disabled'} if 'disabled' in thinking.get('values', []) else {}
    limit = min(max_tokens, int(specs['max_tokens']['max']))
    wire, _ = resolve_parameters(config, overrides, max_tokens=limit, key=key)
    return wire
