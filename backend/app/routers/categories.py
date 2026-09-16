from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user, require_admin
from app.models import Category, User
from app.schemas import CategoryCreate, CategoryOut, CategoryUpdate

router = APIRouter()


@router.get("", response_model=list[CategoryOut])
def list_categories(
    include_archived: bool = Query(default=False),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = select(Category).order_by(Category.sort_order, Category.name)
    if not include_archived:
        query = query.where(Category.is_archived == 0)
    return db.execute(query).scalars().all()


@router.post("", response_model=CategoryOut, status_code=201)
def create_category(
    payload: CategoryCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    existing = db.execute(select(Category).where(Category.name == payload.name)).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="Категория с таким именем уже существует")

    max_order = db.execute(select(Category.sort_order).order_by(Category.sort_order.desc()).limit(1)).scalar_one_or_none()
    category = Category(
        name=payload.name,
        color=payload.color,
        icon=payload.icon,
        sort_order=(max_order or 0) + 1,
    )
    db.add(category)
    db.commit()
    db.refresh(category)
    return category


@router.patch("/{category_id}", response_model=CategoryOut)
def update_category(
    category_id: int,
    payload: CategoryUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    category = db.get(Category, category_id)
    if not category:
        raise HTTPException(status_code=404, detail="Категория не найдена")

    data = payload.model_dump(exclude_unset=True)
    if "name" in data and data["name"] != category.name:
        existing = db.execute(select(Category).where(Category.name == data["name"])).scalar_one_or_none()
        if existing:
            raise HTTPException(status_code=409, detail="Категория с таким именем уже существует")

    for key, value in data.items():
        setattr(category, key, value)

    db.commit()
    db.refresh(category)
    return category


@router.post("/{category_id}/archive", response_model=CategoryOut)
def archive_category(category_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    category = db.get(Category, category_id)
    if not category:
        raise HTTPException(status_code=404, detail="Категория не найдена")
    category.is_archived = 1
    db.commit()
    db.refresh(category)
    return category


@router.post("/{category_id}/unarchive", response_model=CategoryOut)
def unarchive_category(category_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    category = db.get(Category, category_id)
    if not category:
        raise HTTPException(status_code=404, detail="Категория не найдена")
    category.is_archived = 0
    db.commit()
    db.refresh(category)
    return category
