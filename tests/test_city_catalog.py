import pytest

from metro.config import fallback_osm_id, load_config
from scripts.export_webapp import _resolved_place


@pytest.mark.parametrize('requested,canonical,osm_id', [
    ('Metro Manila', 'Metro Manila, Philippines', 'R147488'),
    ('Legazpi', 'Legazpi City, Albay, Philippines', 'R3488060'),
    ('Naga', 'Naga City, Camarines Sur, Philippines', 'R3084673'),
    ('Dipolog City', 'Dipolog City, Zamboanga del Norte, Philippines', 'R20372225'),
    ('Illigan City', 'Iligan City, Philippines', 'R3818838'),
    ('Valencia City, Bukidnon', 'Valencia City, Bukidnon, Philippines', 'R13437285'),
    ('Kabankalan City, Negros', 'Kabankalan City, Negros Occidental, Philippines', 'R3740502'),
    ('San Fernando, Pampanga', 'San Fernando City, Pampanga, Philippines', 'R13263379'),
    ('Baguio City', 'Baguio City, Philippines', 'R13946201'),
])
def test_requested_city_resolves_to_verified_relation(requested, canonical, osm_id):
    cfg = load_config()
    place = _resolved_place(cfg, requested)
    assert place == canonical
    assert fallback_osm_id(cfg, place) == osm_id


@pytest.mark.parametrize('place', [
    'Naga City, Cebu, Philippines', 'San Fernando, La Union, Philippines',
    'Valencia, Negros Oriental, Philippines',
])
def test_ambiguous_names_do_not_reuse_other_province_pin(place):
    cfg = load_config()
    assert _resolved_place(cfg, place) == place
    assert fallback_osm_id(cfg, place) is None
