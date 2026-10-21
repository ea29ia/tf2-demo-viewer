use std::env;
use std::fs;
use tf_demo_parser::demo::parser::gamestateanalyser::GameStateAnalyser;
use tf_demo_parser::{Demo, DemoParser};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let path = env::args().nth(1).ok_or("usage: dem2replay <demo.dem>")?;
    let file = fs::read(path)?;
    let demo = Demo::new(&file);
    let parser = DemoParser::new_with_analyser(demo.get_stream(), GameStateAnalyser::new());
    let (header, mut ticker) = parser.ticker()?;
    println!("{} {} {} ticks={} frames={}", header.map, header.nick, header.duration, header.ticks, header.frames);
    let mut n = 0;
    let mut last = 0u32;
    while let Some(t) = ticker.next()? {
        let tick: u32 = t.tick.into();
        if tick != last {
            last = tick;
            let alive = t.state.players.iter().filter(|p| p.state == tf_demo_parser::demo::data::game_state::PlayerState::Alive).count();
            if n % 3000 == 0 {
                let p = t.state.players.iter().find(|p| p.team.is_player());
                if let Some(p) = p {
                    println!("tick {} players {} alive {} sample {:?} pos {:?} hp {} class {:?} pvs {}", tick, t.state.players.len(), alive, p.info.as_ref().map(|i| i.name.as_str()).unwrap_or("?"), p.position, p.health, p.class, p.in_pvs);
                }
            }
            n += 1;
        }
    }
    println!("frames sampled {}", n);
    Ok(())
}
