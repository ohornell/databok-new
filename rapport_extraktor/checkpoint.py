"""
Checkpoint-system för att spara och återuppta extraktioner.

Säkerställer att vid krasch eller avbrott kan extraktionen fortsätta
från där den slutade istället för att börja om från början.
"""

import json
from pathlib import Path
from datetime import datetime
from typing import TypedDict


class CheckpointData(TypedDict):
    """Data som sparas för varje checkpoint."""
    completed: list[str]  # Lista med färdiga filsökvägar
    failed: list[dict]    # Lista med misslyckade filer och fel
    last_file: str        # Senast processade fil
    last_update: str      # Tidsstämpel för senaste uppdatering
    total_files: int      # Totalt antal filer i batchen
    batch_started: str    # När batchen startade


# Standard checkpoint-fil i rapport_extraktor-mappen
DEFAULT_CHECKPOINT_FILE = Path(__file__).parent / "extraction_checkpoint.json"


def get_checkpoint_file() -> Path:
    """Returnera sökväg till checkpoint-filen."""
    return DEFAULT_CHECKPOINT_FILE


def save_checkpoint(
    batch_id: str,
    completed: list[str],
    failed: list[dict] | None = None,
    last_file: str | None = None,
    total_files: int = 0,
    batch_started: str | None = None
) -> None:
    """
    Spara checkpoint efter varje processad fil.

    Args:
        batch_id: Unikt ID för denna batch (t.ex. company_id + timestamp)
        completed: Lista med sökvägar till färdiga filer
        failed: Lista med dicts {path, error, timestamp} för misslyckade
        last_file: Senast processade fil
        total_files: Totalt antal filer i batchen
        batch_started: Tidsstämpel när batchen startade
    """
    checkpoint_file = get_checkpoint_file()

    # Ladda befintlig data
    data = load_all_checkpoints()

    # Uppdatera denna batch
    data[batch_id] = CheckpointData(
        completed=completed,
        failed=failed or [],
        last_file=last_file or "",
        last_update=datetime.now().isoformat(),
        total_files=total_files,
        batch_started=batch_started or datetime.now().isoformat()
    )

    # Spara atomiskt (skriv till temp, sedan rename)
    temp_file = checkpoint_file.with_suffix(".tmp")
    temp_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    temp_file.replace(checkpoint_file)


def load_all_checkpoints() -> dict[str, CheckpointData]:
    """Ladda alla checkpoints från fil."""
    checkpoint_file = get_checkpoint_file()

    if checkpoint_file.exists():
        try:
            return json.loads(checkpoint_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def load_checkpoint(batch_id: str) -> CheckpointData | None:
    """Ladda checkpoint för en specifik batch."""
    data = load_all_checkpoints()
    return data.get(batch_id)


def get_completed_files(batch_id: str) -> set[str]:
    """
    Returnera set med redan processade filer för en batch.

    Används för att skippa filer vid återstart.
    """
    checkpoint = load_checkpoint(batch_id)
    if checkpoint:
        return set(checkpoint.get("completed", []))
    return set()


def get_failed_files(batch_id: str) -> list[dict]:
    """
    Returnera lista med misslyckade filer för en batch.

    Varje dict innehåller: {path, error, timestamp}
    """
    checkpoint = load_checkpoint(batch_id)
    if checkpoint:
        return checkpoint.get("failed", [])
    return []


def add_completed_file(batch_id: str, file_path: str, total_files: int = 0) -> None:
    """Lägg till en färdig fil till checkpoint."""
    checkpoint = load_checkpoint(batch_id)

    if checkpoint:
        completed = checkpoint.get("completed", [])
        if file_path not in completed:
            completed.append(file_path)

        save_checkpoint(
            batch_id=batch_id,
            completed=completed,
            failed=checkpoint.get("failed", []),
            last_file=file_path,
            total_files=total_files or checkpoint.get("total_files", 0),
            batch_started=checkpoint.get("batch_started")
        )
    else:
        # Ny batch
        save_checkpoint(
            batch_id=batch_id,
            completed=[file_path],
            failed=[],
            last_file=file_path,
            total_files=total_files
        )


def add_failed_file(
    batch_id: str,
    file_path: str,
    error: str,
    total_files: int = 0
) -> None:
    """Lägg till en misslyckad fil till checkpoint."""
    checkpoint = load_checkpoint(batch_id)

    failed_entry = {
        "path": file_path,
        "error": error,
        "timestamp": datetime.now().isoformat()
    }

    if checkpoint:
        failed = checkpoint.get("failed", [])
        # Undvik dubletter
        if not any(f["path"] == file_path for f in failed):
            failed.append(failed_entry)

        save_checkpoint(
            batch_id=batch_id,
            completed=checkpoint.get("completed", []),
            failed=failed,
            last_file=file_path,
            total_files=total_files or checkpoint.get("total_files", 0),
            batch_started=checkpoint.get("batch_started")
        )
    else:
        save_checkpoint(
            batch_id=batch_id,
            completed=[],
            failed=[failed_entry],
            last_file=file_path,
            total_files=total_files
        )


def clear_checkpoint(batch_id: str) -> None:
    """Ta bort checkpoint för en specifik batch."""
    data = load_all_checkpoints()
    if batch_id in data:
        del data[batch_id]
        checkpoint_file = get_checkpoint_file()
        checkpoint_file.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def clear_all_checkpoints() -> None:
    """Ta bort alla checkpoints."""
    checkpoint_file = get_checkpoint_file()
    if checkpoint_file.exists():
        checkpoint_file.unlink()


def get_batch_progress(batch_id: str) -> tuple[int, int, int]:
    """
    Returnera progress för en batch.

    Returns:
        (completed, failed, total)
    """
    checkpoint = load_checkpoint(batch_id)
    if checkpoint:
        completed = len(checkpoint.get("completed", []))
        failed = len(checkpoint.get("failed", []))
        total = checkpoint.get("total_files", 0)
        return (completed, failed, total)
    return (0, 0, 0)


def generate_batch_id(company_id: str, prefix: str = "") -> str:
    """
    Generera ett unikt batch-ID baserat på company_id och tid.

    Format: {prefix}_{company_id}_{date}
    Exempel: batch_abc123_2024-01-15
    """
    date_str = datetime.now().strftime("%Y-%m-%d")
    if prefix:
        return f"{prefix}_{company_id}_{date_str}"
    return f"batch_{company_id}_{date_str}"


def get_resumable_batches() -> list[dict]:
    """
    Returnera lista med batchar som kan återupptas.

    En batch kan återupptas om den har fler filer kvar att processa.
    """
    data = load_all_checkpoints()
    resumable = []

    for batch_id, checkpoint in data.items():
        completed = len(checkpoint.get("completed", []))
        failed = len(checkpoint.get("failed", []))
        total = checkpoint.get("total_files", 0)

        if total > 0 and (completed + failed) < total:
            resumable.append({
                "batch_id": batch_id,
                "completed": completed,
                "failed": failed,
                "total": total,
                "remaining": total - completed - failed,
                "last_update": checkpoint.get("last_update"),
                "batch_started": checkpoint.get("batch_started")
            })

    return resumable
