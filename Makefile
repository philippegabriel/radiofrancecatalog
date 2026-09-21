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
TARGETS:= $(addsuffix .html, $(FIPODCASTS) $(FCPODCASTS))
CSVS:= $(addsuffix .csv, $(FIPODCASTS) $(FCPODCASTS))
CODS:= $(addsuffix .cutOffDate, $(FIPODCASTS) $(FCPODCASTS))
CACHEDCSVS := $(addprefix .cache/,$(CSVS))
all: $(CACHEDCSVS) $(CODS) $(TARGETS)

$(CACHEDCSVS):
	mkdir -p .cache/
	wget -nc -q $(GITHUBPAGE)/$(notdir $@) -O $@ || touch $@

%.cutOffDate: .cache/%.csv
	tail -n 1 $< | cut -d ',' -f1 > $@ || exit 0
	test -s $< || echo '1900-01-01T00:00:00+00:00' > $@

%.db: .cache/%.csv %.csv
	sqlite3 $@ < schema.sql
	sqlite3 $@ ".import --csv --skip 1 $< rf"
	sqlite3 $@ ".import --csv --skip 1 $(word $(words $?),$?) rf"
	sqlite3 $@ < addlinktags.sql

%.db.csv: %.db
	sqlite3 -init sqlite3.csv.init $< < query.sql > $@

login: le-cours-de-l-histoire.db
	sqlite3 -init sqlite3.csv.init le-cours-de-l-histoire.db

$(addsuffix .csv,$(FIPODCASTS)):
	$(eval since := $(shell cat $(subst .csv,.cutOffDate,$@)))
	@echo fetching $@ since $(since) ...
	python rf_dump.py --api-key $(KEY) --since $(since) --show-url $(FI_URL)/$(@:.csv=) --out $@

$(addsuffix .csv,$(FCPODCASTS)):
	$(eval since := $(shell cat $(subst .csv,.cutOffDate,$@)))
	@echo fetching $@ since $(since) ...
	python rf_dump.py --api-key $(KEY) --since $(since) --show-url $(FC_URL)/$(@:.csv=) --out $@

%.html: %.db.csv
	echo '<link rel="stylesheet" href="index.css">' > $@
	python csv2html.py < $< >> $@
test: $(CACHEDCSVS) $(CODS)

clean:
	rm -rf $(TARGETS)
	rm -rf $(addsuffix .db,$(basename $(TARGETS)))
	rm -rf $(addsuffix .db.csv,$(basename $(TARGETS)))

reallyclean: clean
	rm -rf $(CSVS) $(CACHEDCSVS) $(CODS)


