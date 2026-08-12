import os
import re

from fastapi import APIRouter, HTTPException

from ..config import settings
from ..services import scanner

router = APIRouter(prefix="/categories", tags=["categories"])

_AUDIOBOOK_RE = re.compile(r"audiobook", re.IGNORECASE)


@router.get("")
def list_categories():
    return scanner.get_categories(settings.incoming_dir)


@router.get("/{category_name}/items")
def list_items(category_name: str):
    path = os.path.join(settings.incoming_dir, category_name)
    if not os.path.isdir(path):
        raise HTTPException(status_code=404, detail="Category not found")
    audio_bundle_aware = bool(_AUDIOBOOK_RE.search(category_name))
    return scanner.get_items(path, audio_bundle_aware=audio_bundle_aware)


@router.get("/{category_name}/items/{item_name:path}")
def get_item(category_name: str, item_name: str):
    path = os.path.join(settings.incoming_dir, category_name, item_name)
    if not os.path.isdir(path):
        raise HTTPException(status_code=404, detail="Item not found")
    return scanner.get_item_details(path)
