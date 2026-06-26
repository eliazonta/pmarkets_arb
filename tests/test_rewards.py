import math

from pmarket_arb.rewards import RewardProgram


def test_from_market_parses_clob_rewards():
    market = {
        "clobRewards": [{"rewardsDailyRate": 228}, {"rewardsDailyRate": 22}],
        "rewardsMinSize": 1000,
        "rewardsMaxSpread": 1.5,
    }
    rp = RewardProgram.from_market(market)
    assert rp.daily_pool == 250
    assert rp.min_size == 1000 and rp.max_spread_cents == 1.5 and rp.enabled


def test_no_rewards_is_disabled():
    rp = RewardProgram.from_market({})
    assert not rp.enabled
    assert rp.daily_reward(5000, 0, 0.5) == 0.0


def test_qualification_floors():
    rp = RewardProgram(daily_pool=200, min_size=1000, max_spread_cents=1.5)
    assert not rp.qualifies(500, 1.0)      # below min size
    assert not rp.qualifies(2000, 3.0)     # outside reward band
    assert rp.qualifies(2000, 1.0)


def test_proportional_share():
    rp = RewardProgram(daily_pool=300, min_size=1000, max_spread_cents=1.5)
    # our 1000 vs 2000 competing -> 1/3 of the pool
    assert math.isclose(rp.daily_reward(1000, 2000, 1.0), 100.0)
    # alone in the band -> whole pool
    assert math.isclose(rp.daily_reward(1000, 0, 1.0), 300.0)
