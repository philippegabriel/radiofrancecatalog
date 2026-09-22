.PHONY: dump test
KEY=$(file < .OpenAPIKey)
FC_URL := https://www.radiofrance.fr/franceculture/podcasts
FI_URL := https://www.radiofrance.fr/franceinter/podcasts
FIPODCASTS := affaires-sensibles
FCPODCASTS := \
	lsd-la-serie-documentaire \
	les-nuits-de-france-culture \
	les-pieds-sur-terre \
	le-cours-de-l-histoire \
	mecaniques-du-journalisme
GITHUBPAGE := https://philippegabriel.github.io/radiofrancecatalog
PODCASTS:= $(FIPODCASTS) $(FCPODCASTS)
TARGETS:= $(addsuffix .html, $(PODCASTS))
CSVS:= $(addsuffix .csv, $(PODCASTS))
DBS:= $(addsuffix .db, $(PODCASTS))
CODS:= $(addsuffix .cutOffDate, $(PODCASTS))
CACHEDDBS := $(addprefix .cache/,$(CSVS))
all: $(CACHEDBS) $(CODS) $(TARGETS)

.cache/%.csv:
	mkdir -p .cache/
	wget -nc -q $(GITHUBPAGE)/$(notdir $@) -O $@ || touch $@

.cache/%.db:
	mkdir -p .cache/
	wget -nc -q $(GITHUBPAGE)/$(notdir $@) -O $@ || sqlite3 $@ < schema.sql

%.cutOffDate: .cache/%.db
	sqlite3 $< < cutoffdate.sql > $@

%.db: .cache/%.db %.csv
	cp $< $@
	sqlite3 $@ ".import --csv --skip 1 $(word $(words $^),$^) rf"

%.db.csv: %.db
	sqlite3 -init sqlite3.csv.init $< < query.sql > $@

login: le-cours-de-l-histoire.db
	sqlite3 -init sqlite3.csv.init le-cours-de-l-histoire.db

$(addsuffix .csv,$(FIPODCASTS)): %.csv: %.cutOffDate
	$(eval since := $(shell cat  $<))
	@echo fetching $@ since $(since) ...
	python rf_dump.py --api-key $(KEY) --since $(since) --show-url $(FI_URL)/$(@:.csv=) --out $@

$(addsuffix .csv,$(FCPODCASTS)): %.csv: %.cutOffDate
	$(eval since := $(shell cat $<))
	@echo fetching $@ since $(since) ...
	python rf_dump.py --api-key $(KEY) --since $(since) --show-url $(FC_URL)/$(@:.csv=) --out $@

%.html: %.db.csv
	echo '<link rel="stylesheet" href="index.css">' > $@
	python csv2html.py < $< >> $@
test: $(CODS) $(CSVS) $(DBS)

clean:
	rm -rf $(TARGETS) $(CSVS) $(CODS) $(DBS)
	rm -rf $(addsuffix .db.csv,$(basename $(TARGETS)))

reallyclean: clean
	rm -rf $(CACHEDCSVS) 


