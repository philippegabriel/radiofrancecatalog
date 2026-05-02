update rf
set web_url=concat('<a href="',web_url,'">web url</a>') where web_url <> "";
update rf
set podcast_url=concat('<a href="',podcast_url,'">podcast url</a>') where podcast_url <> "";
update rf
set player_url=concat('<a href="',player_url,'">player url</a>') where player_url <> "";
