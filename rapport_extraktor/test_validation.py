"""
Test av valideringsfunktioner.
Kör: py test_validation.py
"""

from validation import (
    is_invalid_label,
    validate_table,
    validate_tables,
    format_validation_report,
)


def test_invalid_labels():
    """Testa att ogiltiga labels identifieras."""
    print("Testing invalid labels...")

    # Dessa ska vara ogiltiga
    invalid = ["1", "2", "123", "label: 1", "label:2", "row 1", "rad 2", "", "   "]
    for label in invalid:
        assert is_invalid_label(label), f"'{label}' borde vara ogiltig"
    print(f"  [OK] {len(invalid)} ogiltiga labels identifierade korrekt")

    # Dessa ska vara giltiga
    valid = [
        "Nettoomsättning",
        "Consumables",
        "Technologies",
        "Rörelseresultat",
        "varav Sverige",
        "Q1 2024",
        "EMEA",
    ]
    for label in valid:
        assert not is_invalid_label(label), f"'{label}' borde vara giltig"
    print(f"  [OK] {len(valid)} giltiga labels identifierade korrekt")


def test_validate_table():
    """Testa validering av enskild tabell."""
    print("\nTesting table validation...")

    # Tabell med fel (simulerar segment-problemet)
    bad_table = {
        "id": "table_1",
        "title": "Nettoomsättning per segment",
        "columns": ["", "Q2 2025", "Q2 2024"],
        "rows": [
            {"label": "Nettoomsättning, varav:", "values": [None, 309, 328], "order": 1},
            {"label": "1", "values": [None, 134, 139], "order": 2},  # FEL!
            {"label": "2", "values": [None, 77, 76], "order": 3},    # FEL!
            {"label": "label: 3", "values": [None, 98, 113], "order": 4},  # FEL!
        ]
    }

    errors = validate_table(bad_table)
    assert len(errors) == 3, f"Förväntat 3 fel, fick {len(errors)}"
    print(f"  [OK] Hittade {len(errors)} fel i dalig tabell")

    # Korrekt tabell
    good_table = {
        "id": "table_2",
        "title": "Nettoomsättning per segment",
        "columns": ["", "Q2 2025", "Q2 2024"],
        "rows": [
            {"label": "Nettoomsättning, varav:", "values": [None, 309, 328], "order": 1},
            {"label": "Consumables", "values": [None, 134, 139], "order": 2, "indent": 1},
            {"label": "Technologies", "values": [None, 77, 76], "order": 3, "indent": 1},
            {"label": "Genetics", "values": [None, 98, 113], "order": 4, "indent": 1},
        ]
    }

    errors = validate_table(good_table)
    assert len(errors) == 0, f"Förväntat 0 fel, fick {len(errors)}"
    print("  [OK] Ingen fel i korrekt tabell")


def test_values_length_mismatch():
    """Testa att values-längd valideras."""
    print("\nTesting values length validation...")

    table = {
        "id": "table_3",
        "title": "Test",
        "columns": ["", "Q2 2025", "Q2 2024", "Q1 2025"],  # 4 kolumner
        "rows": [
            {"label": "Rad 1", "values": [None, 100, 200], "order": 1},  # FEL: 3 values, 4 columns
        ]
    }

    errors = validate_table(table)
    mismatch_errors = [e for e in errors if e.error_type == "values_length_mismatch"]
    assert len(mismatch_errors) == 1, "Förväntat 1 values_length_mismatch"
    print("  [OK] Values-langd valideras korrekt")


def test_empty_table():
    """Testa att tomma tabeller identifieras."""
    print("\nTesting empty table validation...")

    table = {
        "id": "table_4",
        "title": "Tom tabell",
        "columns": ["", "Q2 2025"],
        "rows": []  # Inga rader
    }

    errors = validate_table(table)
    empty_errors = [e for e in errors if e.error_type == "empty_table"]
    assert len(empty_errors) == 1, "Förväntat 1 empty_table"
    print("  [OK] Tomma tabeller identifieras korrekt")


def test_format_report():
    """Testa rapportformatering."""
    print("\nTesting report formatting...")

    tables = [
        {
            "id": "t1",
            "title": "Tabell med fel",
            "columns": ["", "Q2"],
            "rows": [
                {"label": "1", "values": [None, 100], "order": 1},
            ]
        }
    ]

    result = validate_tables(tables)
    report = format_validation_report(result)

    assert "fel" in report.lower() or "error" in report.lower(), "Rapport ska innehalla fel"
    assert "invalid_label" in report, "Rapport ska visa error_type"
    print("  [OK] Rapport formateras korrekt")
    print(f"\n  Exempelrapport:\n{report}")


if __name__ == "__main__":
    print("=" * 60)
    print("VALIDERING: ENHETSTESTER")
    print("=" * 60)

    test_invalid_labels()
    test_validate_table()
    test_values_length_mismatch()
    test_empty_table()
    test_format_report()

    print("\n" + "=" * 60)
    print("[OK] ALLA TESTER GODKANDA")
    print("=" * 60)
