"""@file export.py
@brief Esportazione delle ricette in JSON per l'Edge Controller.

@details Non esiste ancora un canale di comunicazione diretto fra backend e Edge
Controller. L'unico punto di ingresso gia disponibile lato C++ e
`load_recipe_json(path)` (edge/src/recipe_json.cpp), che legge un file da
un percorso passato esplicitamente: questo modulo scrive li lo stesso JSON
che il backend valida e salva, cosi l'Edge puo caricarlo senza modifiche.

`recipe.id` arriva da una richiesta HTTP esterna e diventa qui un nome di
file: senza controlli un id come ``"../../etc/passwd"`` scriverebbe fuori
dalla cartella di esportazione. Il controllo rifiuta i separatori di
percorso e verifica che il percorso finale resti dentro la cartella,
invece di fidarsi soltanto di un filtro sui caratteri.
"""

from pathlib import Path

from .models import Recipe

## @brief Directory predefinita dei file JSON destinati all'Edge Controller.
DEFAULT_EXPORT_DIRECTORY = (
    Path(__file__).resolve().parents[3] / "data" / "recipes"
)


class UnsafeRecipeId(ValueError):
    """@brief Segnala un identificativo non utilizzabile come nome di file."""


def _export_path(recipe_id: str, directory: Path) -> Path:
    """@brief Costruisce e valida il percorso del file esportato.

    @param recipe_id Identificativo della ricetta usato come nome del file.
    @param directory Directory che deve contenere il percorso risultante.
    @return Percorso assoluto del file JSON.
    @throws UnsafeRecipeId Se l'identificativo contiene separatori o produce un
        percorso esterno alla directory consentita.
    """
    if "/" in recipe_id or "\\" in recipe_id:
        raise UnsafeRecipeId(
            f"recipe id {recipe_id!r} must not contain a path separator")

    resolved_directory = directory.resolve()
    path = (resolved_directory / f"{recipe_id}.json").resolve()
    if resolved_directory not in path.parents:
        raise UnsafeRecipeId(f"recipe id {recipe_id!r} is not a safe file name")
    return path


def assert_safe_recipe_id(recipe_id: str, directory: Path = DEFAULT_EXPORT_DIRECTORY) -> None:
    """@brief Verifica che un identificativo produca un percorso sicuro.

    @details Non scrive nulla: serve a rifiutare un id pericoloso prima di salvare
    la ricetta in SQLite.

    @param recipe_id Identificativo da verificare.
    @param directory Directory entro la quale deve rimanere il file.
    @return Nessun valore.
    @throws UnsafeRecipeId Se l'id contiene un separatore di percorso o
        permetterebbe di uscire da `directory`.
    """
    _export_path(recipe_id, directory)


def export_recipe_for_edge(recipe: Recipe, directory: Path = DEFAULT_EXPORT_DIRECTORY) -> Path:
    """@brief Esporta una ricetta in `<directory>/<recipe.id>.json`.

    @details Il contenuto e l'indentazione a 2 spazi rispecchiano
    `recipe_to_json()` e `save_recipe_json()` lato Edge, cosi il file puo
    essere caricato da ``load_recipe_json`` senza modifiche.

    @param recipe Ricetta validata da serializzare.
    @param directory Directory di destinazione.
    @return Percorso del file JSON scritto.
    @throws UnsafeRecipeId Se l'id non e un nome di file sicuro.
    """
    path = _export_path(recipe.id, directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(recipe.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path
