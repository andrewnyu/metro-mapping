import pytest

from metro.config import fallback_osm_id, load_config
from scripts.export_webapp import _resolved_place


@pytest.mark.parametrize('requested,canonical,osm_id', [
    ('General Santos City', 'General Santos City, Philippines', 'R14144757'),
    ('Dagupan', 'Dagupan, Pangasinan, Philippines', 'R13001749'),
    ('Cauayan Isabela', 'Cauayan, Isabela, Philippines', 'R19646111'),
    ('Laoag Ilocos', 'Laoag, Ilocos Norte, Philippines', 'N317949136'),
    ('Lucena City', 'Lucena, Quezon, Philippines', 'R11124741'),
    ('Pagadian City', 'Pagadian, Zamboanga del Sur, Philippines', 'N965797331'),
    ('Tandag City (Surigao del Sur)', 'Tandag, Surigao del Sur, Philippines', 'N198522530'),
    ('Kidapawan City', 'Kidapawan, Cotabato, Philippines', 'R1513759'),
    ('Koronadal City', 'Koronadal, South Cotabato, Philippines', 'R10903143'),
    ('Tacurong City', 'Tacurong, Sultan Kudarat, Philippines', 'R20038093'),
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
