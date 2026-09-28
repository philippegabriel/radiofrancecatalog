.PHONY: dump test login resetdb clean reallyclean
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
CSVS:= $(addsuffix .csv, $(PODCASTS))
RFCSVS:= $(addsuffix .rf.csv, $(PODCASTS))
DBS:= $(addsuffix .db, $(PODCASTS))
CODS:= $(addsuffix .cutOffDate, $(PODCASTS))
CACHEDDBS := $(addprefix .cache/,$(DBS))
CACHEDCSVS := $(addprefix .cache/,$(CSVS))
TARGETS:= $(CSVS) $(addsuffix .html, $(PODCASTS))
PSQL := psql -X -v ON_ERROR_STOP=1 -U pgabriel template1
all: $(CACHEDCSVS) $(CACHEDDBS) $(CODS) $(TARGETS)

.cache/%.csv:
	mkdir -p .cache/
	wget -nc -q $(GITHUBPAGE)/$(notdir $@) -O $@ || touch $@

.cache/%.db: .cache/%.csv
	mkdir -p .cache/
	rm -f $@
	$(PSQL) -f schema.sql
	$(PSQL)  -c "\copy rf FROM $< DELIMITER ',' CSV HEADER"
	touch $@

%.cutOffDate: .cache/%.db
	$(PSQL) --tuples-only -v show=$* -f cutoffdate.sql -o $@

%.db: %.rf.csv .cache/%.db
	$(PSQL) -c "\copy rf FROM $< DELIMITER ',' CSV HEADER"
	touch $@

%.csv: emitcsv.sql %.db
	$(PSQL) --csv -v show="$*" -o $@ -f $<

%.html.csv: emithtml.sql %.db
	$(PSQL) --csv -v show="$*" -o $@ -f $<

login:
	$(PSQL)

$(addsuffix .rf.csv,$(FIPODCASTS)): %.rf.csv: %.cutOffDate
	$(eval since := $(shell cat  $<))
	@echo fetching $@ since $(since) ...
	python rf_dump.py --api-key $(KEY) --since $(since) --show-url $(FI_URL)/$(@:.rf.csv=) --out $@

$(addsuffix .rf.csv,$(FCPODCASTS)): %.rf.csv: %.cutOffDate
	$(eval since := $(shell cat $<))
	@echo fetching $@ since $(since) ...
	python rf_dump.py --api-key $(KEY) --since $(since) --show-url $(FC_URL)/$(@:.rf.csv=) --out $@

%.html: %.html.csv
	echo '<link rel="stylesheet" href="index.css">' > $@
	echo '<img src="Logo_Radio_France.svg.webp" alt="Radio France">' >> $@
	python csv2html.py < $< >> $@

test:
	@echo test

resetdb:
	psql -U pgabriel template1 -f droptables.sql
	rm -f .cache/*.db *.db
clean: resetdb
	rm -rf $(TARGETS) $(RFCSVS) $(CSVS) $(CODS) $(DBS) $(CACHEDDBS)
reallyclean: clean
	rm -rf $(CACHEDCSVS)


