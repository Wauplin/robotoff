import abc
import datetime
from typing import Optional, List

from dataclasses import dataclass
from enum import Enum

from robotoff.insights._enum import InsightType
from robotoff.insights.normalize import normalize_emb_code
from robotoff.models import db, ProductInsight, UserAnnotation
from robotoff.off import (
    get_product,
    update_emb_codes,
    add_label_tag,
    add_category,
    update_quantity,
    update_expiration_date,
    add_brand,
    add_store,
    add_packaging,
    OFFAuthentication,
)
from robotoff.utils import get_logger
from robotoff.utils.types import JSONType

logger = get_logger(__name__)


@dataclass
class AnnotationResult:
    status: str
    description: Optional[str] = None


class AnnotationStatus(Enum):
    saved = 1
    updated = 2
    error_missing_product = 3
    error_updated_product = 4
    error_already_annotated = 5
    error_unknown_insight = 6


SAVED_ANNOTATION_RESULT = AnnotationResult(
    status=AnnotationStatus.saved.name, description="the annotation was saved"
)
UPDATED_ANNOTATION_RESULT = AnnotationResult(
    status=AnnotationStatus.updated.name,
    description="the annotation was saved and sent to OFF",
)
MISSING_PRODUCT_RESULT = AnnotationResult(
    status=AnnotationStatus.error_missing_product.name,
    description="the product could not be found on OFF",
)
ALREADY_ANNOTATED_RESULT = AnnotationResult(
    status=AnnotationStatus.error_already_annotated.name,
    description="the insight has already been annotated",
)
UNKNOWN_INSIGHT_RESULT = AnnotationResult(
    status=AnnotationStatus.error_unknown_insight.name, description="unknown insight ID"
)


def extract_username(session_cookie: str) -> Optional[str]:
    splitted = session_cookie.split("&")

    if splitted:
        is_next = False
        for split in splitted:
            if split == "user_id":
                is_next = True
                continue
            elif is_next:
                if split:
                    return split
                else:
                    break

    logger.warning(
        "Unable to extract username from session cookie: {}".format(session_cookie)
    )
    return None


class InsightAnnotator(metaclass=abc.ABCMeta):
    def annotate(
        self,
        insight: ProductInsight,
        annotation: int,
        update=True,
        auth: Optional[OFFAuthentication] = None,
    ) -> AnnotationResult:
        username: Optional[str] = None
        if auth is not None:
            username = auth.username

            if auth.session_cookie:
                username = extract_username(auth.session_cookie)

        with db.atomic():
            insight.annotation = annotation
            insight.completed_at = datetime.datetime.utcnow()
            insight.save()

            if username:
                UserAnnotation.create(insight=insight, username=username)

        if annotation == 1 and update:
            return self.update_product(insight, auth=auth)

        return SAVED_ANNOTATION_RESULT

    @abc.abstractmethod
    def update_product(
        self, insight: ProductInsight, auth: Optional[OFFAuthentication] = None
    ) -> AnnotationResult:
        pass


class PackagerCodeAnnotator(InsightAnnotator):
    def update_product(
        self, insight: ProductInsight, auth: Optional[OFFAuthentication] = None
    ) -> AnnotationResult:
        emb_code: str = insight.value

        product = get_product(insight.barcode, ["emb_codes"])

        if product is None:
            return MISSING_PRODUCT_RESULT

        emb_codes_str: str = product.get("emb_codes", "")

        emb_codes: List[str] = []
        if emb_codes_str:
            emb_codes = emb_codes_str.split(",")

        if self.already_exists(emb_code, emb_codes):
            return ALREADY_ANNOTATED_RESULT

        emb_codes.append(emb_code)
        update_emb_codes(
            insight.barcode,
            emb_codes,
            server_domain=insight.server_domain,
            insight_id=insight.id,
            auth=auth,
        )
        return UPDATED_ANNOTATION_RESULT

    @staticmethod
    def already_exists(new_emb_code: str, emb_codes: List[str]) -> bool:
        emb_codes = [normalize_emb_code(emb_code) for emb_code in emb_codes]

        normalized_emb_code = normalize_emb_code(new_emb_code)

        if normalized_emb_code in emb_codes:
            return True

        return False


class LabelAnnotator(InsightAnnotator):
    def update_product(
        self, insight: ProductInsight, auth: Optional[OFFAuthentication] = None
    ) -> AnnotationResult:
        product = get_product(insight.barcode, ["labels_tags"])

        if product is None:
            return MISSING_PRODUCT_RESULT

        labels_tags: List[str] = product.get("labels_tags") or []

        if insight.value_tag in labels_tags:
            return ALREADY_ANNOTATED_RESULT

        add_label_tag(
            insight.barcode,
            insight.value_tag,
            insight_id=insight.id,
            server_domain=insight.server_domain,
            auth=auth,
        )

        return UPDATED_ANNOTATION_RESULT


class CategoryAnnotator(InsightAnnotator):
    def update_product(
        self, insight: ProductInsight, auth: Optional[OFFAuthentication] = None
    ) -> AnnotationResult:
        product = get_product(insight.barcode, ["categories_tags"])

        if product is None:
            return MISSING_PRODUCT_RESULT

        categories_tags: List[str] = product.get("categories_tags") or []

        if insight.value_tag in categories_tags:
            return ALREADY_ANNOTATED_RESULT

        category_tag = insight.value_tag
        add_category(
            insight.barcode,
            category_tag,
            insight_id=insight.id,
            server_domain=insight.server_domain,
            auth=auth,
        )

        return UPDATED_ANNOTATION_RESULT


class ProductWeightAnnotator(InsightAnnotator):
    def update_product(
        self, insight: ProductInsight, auth: Optional[OFFAuthentication] = None
    ) -> AnnotationResult:
        product = get_product(insight.barcode, ["quantity"])

        if product is None:
            return MISSING_PRODUCT_RESULT

        quantity: Optional[str] = product.get("quantity") or None

        if quantity is not None:
            return ALREADY_ANNOTATED_RESULT

        update_quantity(
            insight.barcode,
            insight.value,
            insight_id=insight.id,
            server_domain=insight.server_domain,
            auth=auth,
        )

        return UPDATED_ANNOTATION_RESULT


class ExpirationDateAnnotator(InsightAnnotator):
    def update_product(
        self, insight: ProductInsight, auth: Optional[OFFAuthentication] = None
    ) -> AnnotationResult:
        product = get_product(insight.barcode, ["expiration_date"])

        if product is None:
            return MISSING_PRODUCT_RESULT

        current_expiration_date = product.get("expiration_date") or None

        if current_expiration_date:
            return ALREADY_ANNOTATED_RESULT

        update_expiration_date(
            insight.barcode,
            insight.value,
            insight_id=insight.id,
            server_domain=insight.server_domain,
            auth=auth,
        )
        return UPDATED_ANNOTATION_RESULT


class BrandAnnotator(InsightAnnotator):
    def update_product(
        self, insight: ProductInsight, auth: Optional[OFFAuthentication] = None
    ) -> AnnotationResult:
        product = get_product(insight.barcode, ["brands_tags"])

        if product is None:
            return MISSING_PRODUCT_RESULT

        add_brand(
            insight.barcode,
            insight.value,
            insight_id=insight.id,
            server_domain=insight.server_domain,
            auth=auth,
        )
        return UPDATED_ANNOTATION_RESULT


class StoreAnnotator(InsightAnnotator):
    def update_product(
        self, insight: ProductInsight, auth: Optional[OFFAuthentication] = None
    ) -> AnnotationResult:
        product = get_product(insight.barcode, ["stores_tags"])

        if product is None:
            return MISSING_PRODUCT_RESULT

        stores_tags: List[str] = product.get("stores_tags") or []

        if insight.value_tag in stores_tags:
            return ALREADY_ANNOTATED_RESULT

        add_store(
            insight.barcode,
            insight.value,
            insight_id=insight.id,
            server_domain=insight.server_domain,
            auth=auth,
        )
        return UPDATED_ANNOTATION_RESULT


class PackagingAnnotator(InsightAnnotator):
    def update_product(
        self, insight: ProductInsight, auth: Optional[OFFAuthentication] = None
    ) -> AnnotationResult:
        packaging_tag: str = insight.value_tag

        product = get_product(insight.barcode, ["packaging_tags"])

        if product is None:
            return MISSING_PRODUCT_RESULT

        packaging_tags: List[str] = product.get("packaging_tags") or []

        if packaging_tag in packaging_tags:
            return ALREADY_ANNOTATED_RESULT

        add_packaging(
            insight.barcode,
            insight.value,
            insight_id=insight.id,
            server_domain=insight.server_domain,
            auth=auth,
        )
        return UPDATED_ANNOTATION_RESULT


class InsightAnnotatorFactory:
    mapping = {
        InsightType.packager_code.name: PackagerCodeAnnotator(),
        InsightType.label.name: LabelAnnotator(),
        InsightType.category.name: CategoryAnnotator(),
        InsightType.product_weight.name: ProductWeightAnnotator(),
        InsightType.expiration_date.name: ExpirationDateAnnotator(),
        InsightType.brand.name: BrandAnnotator(),
        InsightType.store.name: StoreAnnotator(),
        InsightType.packaging.name: PackagingAnnotator(),
    }

    @classmethod
    def get(cls, identifier: str) -> InsightAnnotator:
        if identifier not in cls.mapping:
            raise ValueError("unknown annotator: {}".format(identifier))

        return cls.mapping[identifier]
