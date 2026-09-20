import pytest
from pyproj import Geod

from urbanflow.ui.area_selector import bbox_feature, square_bbox


def test_square_bbox_is_one_kilometre_by_one_kilometre():
    bbox = square_bbox(53.9, 27.56)
    geod = Geod(ellps="WGS84")
    _, _, width = geod.inv(bbox.west, 53.9, bbox.east, 53.9)
    _, _, height = geod.inv(27.56, bbox.south, 27.56, bbox.north)
    assert width == pytest.approx(1_000, abs=0.01)
    assert height == pytest.approx(1_000, abs=0.01)


def test_square_bbox_moves_with_center():
    first = square_bbox(53.9, 27.56)
    second = square_bbox(53.91, 27.58)
    assert second.west > first.west
    assert second.south > first.south


def test_bbox_feature_uses_persisted_coordinates():
    bbox = square_bbox(53.9, 27.56)
    feature = bbox_feature(bbox)
    ring = feature["geometry"]["coordinates"][0]
    assert ring[0] == [bbox.west, bbox.south]
    assert ring[2] == [bbox.east, bbox.north]
    assert ring[0] == ring[-1]
