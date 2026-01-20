"""
Validering av extraherad data med automatisk retry vid fel.

Fas 1-valideringar:
- Labels: Kontrollera att radnamn är verklig text (inte "1", "label: 2", etc.)
- Values-längd: Antal values måste matcha antal columns
- Tomma tabeller: Varje tabell måste ha minst 1 rad
"""

import re
from dataclasses import dataclass, field


@dataclass
class ValidationError:
    """Ett valideringsfel."""
    table_id: str
    table_title: str
    error_type: str
    message: str
    row_index: int | None = None
    severity: str = "error"  # "error" eller "warning"


@dataclass
class ValidationResult:
    """Resultat av validering."""
    is_valid: bool
    errors: list[ValidationError] = field(default_factory=list)
    warnings: list[ValidationError] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0

    @property
    def has_warnings(self) -> bool:
        return len(self.warnings) > 0

    @property
    def tables_with_errors(self) -> set[str]:
        """Returnera set av tabell-IDs med fel."""
        return {e.table_id for e in self.errors}


# Mönster som indikerar ogiltiga labels
INVALID_LABEL_PATTERNS = [
    r'^(\d+)$',                    # Bara siffror: "1", "2", "123"
    r'^label:\s*\d+$',             # "label: 1", "label:2"
    r'^row\s*\d+$',                # "row 1", "row2"
    r'^rad\s*\d+$',                # "rad 1", "rad2"
    r'^\s*$',                      # Tom eller bara whitespace
]


def is_invalid_label(label: str) -> bool:
    """
    Kontrollera om en label är ogiltig.

    Ogiltiga labels:
    - Bara siffror
    - Generiska placeholders som "label: 1"
    - Tomma strängar
    """
    if not label:
        return True

    label_lower = label.lower().strip()

    for pattern in INVALID_LABEL_PATTERNS:
        if re.match(pattern, label_lower, re.IGNORECASE):
            return True

    return False


def validate_table(table: dict) -> list[ValidationError]:
    """
    Validera en enskild tabell.

    Returnerar lista med fel (tom om allt är OK).
    """
    errors = []
    table_id = table.get("id", "unknown")
    table_title = table.get("title", "Okänd tabell")
    columns = table.get("columns", [])
    rows = table.get("rows", [])

    # Validering 1: Tomma tabeller
    if not rows:
        errors.append(ValidationError(
            table_id=table_id,
            table_title=table_title,
            error_type="empty_table",
            message=f"Tabellen har inga rader"
        ))
        return errors  # Ingen mening att fortsätta om inga rader finns

    num_columns = len(columns)

    for i, row in enumerate(rows):
        label = row.get("label", "")
        values = row.get("values", [])

        # Validering 2: Ogiltiga labels
        if is_invalid_label(label):
            errors.append(ValidationError(
                table_id=table_id,
                table_title=table_title,
                error_type="invalid_label",
                message=f"Ogiltig label '{label}' - ska vara verklig text från PDF",
                row_index=i
            ))

        # Validering 3: Values-längd matchar columns
        if num_columns > 0 and len(values) != num_columns:
            errors.append(ValidationError(
                table_id=table_id,
                table_title=table_title,
                error_type="values_length_mismatch",
                message=f"Rad '{label}' har {len(values)} values men {num_columns} columns",
                row_index=i
            ))

        # Validering 4: Första värdet ska vara null (label-kolumnen)
        if values and values[0] is not None:
            errors.append(ValidationError(
                table_id=table_id,
                table_title=table_title,
                error_type="first_value_not_null",
                message=f"Rad '{label}' har första värdet {values[0]} istället för null",
                row_index=i,
                severity="warning"
            ))

    return errors


def validate_tables(tables: list[dict]) -> ValidationResult:
    """
    Validera alla tabeller.

    Returnerar ValidationResult med alla fel och varningar.
    """
    all_errors = []
    all_warnings = []

    for table in tables:
        errors = validate_table(table)
        for error in errors:
            if error.severity == "warning":
                all_warnings.append(error)
            else:
                all_errors.append(error)

    return ValidationResult(
        is_valid=len(all_errors) == 0,
        errors=all_errors,
        warnings=all_warnings
    )


def format_validation_report(result: ValidationResult) -> str:
    """
    Formatera valideringsresultat som läsbar text.
    """
    lines = []

    if result.is_valid and not result.has_warnings:
        return "[OK] Alla tabeller validerade utan fel"

    if result.has_errors:
        lines.append(f"[FEL] {len(result.errors)} valideringsfel:")
        for error in result.errors:
            row_info = f" (rad {error.row_index})" if error.row_index is not None else ""
            lines.append(f"   [{error.error_type}] {error.table_title}{row_info}: {error.message}")

    if result.has_warnings:
        lines.append(f"\n[VARNING] {len(result.warnings)} varningar:")
        for warning in result.warnings:
            row_info = f" (rad {warning.row_index})" if warning.row_index is not None else ""
            lines.append(f"   [{warning.error_type}] {warning.table_title}{row_info}: {warning.message}")

    return "\n".join(lines)


def get_retry_prompt_for_table(table: dict, errors: list[ValidationError]) -> str:
    """
    Generera en förstärkt prompt för att korrigera en specifik tabell.

    Denna prompt skickas tillsammans med original-PDF:en för retry.
    """
    table_title = table.get("title", "Okänd tabell")
    table_page = table.get("page", "?")

    error_descriptions = []
    for error in errors:
        if error.error_type == "invalid_label":
            error_descriptions.append(
                f"- Rad {error.row_index}: Label '{table.get('rows', [])[error.row_index].get('label', '')}' "
                f"är inte ett riktigt radnamn. Läs av den faktiska texten från PDF:en."
            )
        elif error.error_type == "values_length_mismatch":
            error_descriptions.append(
                f"- Rad {error.row_index}: Antal values matchar inte antal columns."
            )
        elif error.error_type == "empty_table":
            error_descriptions.append(
                f"- Tabellen har inga rader extraherade."
            )

    prompt = f"""KORRIGERA TABELL: {table_title} (sida {table_page})

Föregående extraktion hade följande problem:
{chr(10).join(error_descriptions)}

KRITISKA INSTRUKTIONER:
1. Varje rad MÅSTE ha "label" med den FAKTISKA texten som visas i PDF:en
2. ALDRIG generiska labels som "1", "2", "label: 1" - det är FEL
3. Om raden är indenterad, sätt "indent": 1 (eller 2 för djupare nivå)
4. Antal values MÅSTE matcha antal columns exakt
5. Första värdet i values MÅSTE vara null

Extrahera tabellen "{table_title}" igen med korrekta labels.
"""
    return prompt


# Convenience-funktion för enkel användning
def validate_extraction_result(data: dict) -> ValidationResult:
    """
    Validera ett komplett extraktionsresultat.

    Args:
        data: Dict med "tables", "sections", etc.

    Returns:
        ValidationResult med alla fel och varningar
    """
    tables = data.get("tables", [])
    return validate_tables(tables)
