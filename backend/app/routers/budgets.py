from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.db import get_db
from app.deps import get_current_user, require_admin
from app.models import Budget, Category, Setting, User
from app.schemas import BudgetCategoryOut, BudgetsOut, BudgetsUpdate

router = APIRouter()


def _budgets_out(db: Session) -> BudgetsOut:
    setting = db.get(Setting, "total_monthly_budget_huf")
    total = float(setting.value) if setting and setting.value else None
    budgets = db.execute(select(Budget).options(joinedload(Budget.category))).scalars().all()
    return BudgetsOut(
        total_monthly_budget_huf=total,
        categories=[BudgetCategoryOut(category=b.category, limit_huf=float(b.limit_huf)) for b in budgets],
    )


@router.get("", response_model=BudgetsOut)
def get_budgets(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _budgets_out(db)


@router.put("", response_model=BudgetsOut)
def set_budgets(payload: BudgetsUpdate, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    if payload.total_monthly_budget_huf is not None:
        setting = db.get(Setting, "total_monthly_budget_huf")
        if setting:
            setting.value = str(payload.total_monthly_budget_huf)
        else:
            db.add(Setting(key="total_monthly_budget_huf", value=str(payload.total_monthly_budget_huf)))

    for item in payload.categories:
        category = db.get(Category, item.category_id)
        if not category:
            raise HTTPException(status_code=404, detail=f"Категория {item.category_id} не найдена")
        budget = db.get(Budget, item.category_id)
        if budget:
            budget.limit_huf = item.limit_huf
        else:
            db.add(Budget(category_id=item.category_id, limit_huf=item.limit_huf))

    db.commit()
    return _budgets_out(db)
