"""Explicit absolute USB geometry limit; this is not SI qualification."""
import math

DEFAULT_USB_MAX_LENGTH_NM=20_000_000


def resolve_usb_max_length_nm(max_length_nm=...):
    """Omitted means legacy20mm; explicitNone removes only the absolute cap."""
    if max_length_nm is Ellipsis:return DEFAULT_USB_MAX_LENGTH_NM
    if max_length_nm is None:return None
    if type(max_length_nm) is not int or max_length_nm<=0:
        raise ValueError('USB max_length_nm must be None or a positive integer nm')
    return max_length_nm


def usb_length_allowed(length_nm,max_length_nm):
    """Check actual finite positive length against an already resolved policy."""
    return math.isfinite(length_nm) and length_nm>0 and (max_length_nm is None or length_nm<max_length_nm)


def parse_usb_max_length_mm(text):
    """CLI millimetres, or 'none' to omit the absolute length cap only."""
    if text == 'none':
        return None
    from decimal import Decimal, InvalidOperation
    try:
        nm = Decimal(text) * 1_000_000
        if not nm.is_finite() or nm <= 0 or nm != nm.to_integral_value():
            raise ValueError('USB length must be positive whole nanometres, expressed in mm, or none')
        return resolve_usb_max_length_nm(int(nm))
    except (InvalidOperation, TypeError) as error:
        raise ValueError('USB length must be positive millimetres or none') from error


def configured_usb_max_length_nm(cfg):
    """Read the caller-declared policy; never infer permission from a proof."""
    return resolve_usb_max_length_nm(cfg.get('routing_preparation', {}).get('usb_max_length_nm', ...))
