# Import all models so SQLAlchemy sees them at metadata creation time
from app.models.models import *  # noqa
from app.models.transaction import *  # noqa
from app.models.procurement_models import *  # noqa
from app.models.feedback import *  # noqa
