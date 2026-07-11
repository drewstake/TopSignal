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
    assert normalize_points_basis("nq") == "NQ"
    assert normalize_points_basis("ES") == "ES"


def test_normalize_points_basis_rejects_unknown_values():
    try:
        normalize_points_basis("CL")
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


def test_default_instrument_specs_include_micro_and_emini_index_contracts():
    expected = {
        "MNQ": (0.25, 0.50, 2.0),
        "MES": (0.25, 1.25, 5.0),
        "NQ": (0.25, 5.00, 20.0),
        "ES": (0.25, 12.50, 50.0),
    }

    for symbol, (tick_size, tick_value, point_value) in expected.items():
        spec = DEFAULT_INSTRUMENT_SPECS[symbol]
        assert (spec.tick_size, spec.tick_value) == (tick_size, tick_value)
        assert spec.point_value == point_value
