from app.services.instruments import (
    DEFAULT_INSTRUMENT_SPECS,
    build_point_value_lookup,
    normalize_points_basis,
    resolve_point_value,
)


def test_normalize_points_basis_accepts_auto_and_known_symbols():
    assert normalize_points_basis("auto") == "auto"
    assert normalize_points_basis("mnq") == "MNQ"
    assert normalize_points_basis("MES") == "MES"


def test_normalize_points_basis_rejects_unknown_values():
    try:
        normalize_points_basis("NQ")
    except ValueError as exc:
        assert "pointsBasis must be one of" in str(exc)
    else:
        raise AssertionError("normalize_points_basis should reject unknown symbols")


def test_resolve_point_value_supports_contract_id_symbol_variants():
    point_values = build_point_value_lookup(DEFAULT_INSTRUMENT_SPECS)

    from_symbol = resolve_point_value(symbol="MGC", contract_id=None, point_value_by_symbol=point_values)
    from_contract_id = resolve_point_value(
        symbol=None,
        contract_id="CON.F.US.MES.H26",
        point_value_by_symbol=point_values,
    )

    assert from_symbol == 10.0
    assert from_contract_id == 5.0
