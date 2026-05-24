import os

from fastapi import APIRouter, HTTPException

from ..config import settings
from ..services import scanner

router = APIRouter(prefix="/categories", tags=["categories"])


@router.get("")
def list_categories():
    return scanner.get_categories(settings.incoming_dir)


@router.get("/{category_name}/items")
def list_items(category_name: str):
    path = os.path.join(settings.incoming_dir, category_name)
    if not os.path.isdir(path):
        raise HTTPException(status_code=404, detail="Category not found")
    return scanner.get_items(path)


@router.get("/{category_name}/items/{item_name}")
def get_item(category_name: str, item_name: str):
    path = os.path.join(settings.incoming_dir, category_name, item_name)
    if not os.path.isdir(path):
        raise HTTPException(status_code=404, detail="Item not found")
    return scanner.get_item_details(path)
