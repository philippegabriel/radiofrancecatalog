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
PSQL := psql -X -v ON_ERROR_STOP=1 -U pgabriel radiofrance
TRCSVS:=$(addsuffix .csv,$(addprefix .cache/transcripts/,$(basename $(shell ls ./transcripts/))))
TRCDBS:=$(subst .csv,.db, $(TRCSVS))
CHUNKCSVS:=$(addsuffix .csv,$(addprefix .cache/chunks/,$(basename $(shell ls ./transcripts/))))
CHUNKDBS:=$(subst .csv,.db, $(CHUNKCSVS))
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

.cache/transcripts/%.csv: transcripts/%.json
	@mkdir -p $(@D)
	./transcript_json_to_csv.py $< > $@

.cache/transcripts/%.db: .cache/transcripts/%.csv inserttranscript.sql
	cat $< | $(PSQL) \
	    -v episode_id=$* \
	    -f inserttranscript.sql
	@touch $@

.cache/chunks/%.csv: transcripts/%.json chunk_json_to_csv.py
	@mkdir -p $(dir $@)
	python chunk_json_to_csv.py $< > $@

.cache/chunks/%.db: .cache/chunks/%.csv insertchunks.sql
	cat $< | $(PSQL) \
	    -v episode_id=$* \
	    -f insertchunks.sql
	@touch $@


tr: $(TRCSVS) $(TRCDBS)
	@echo transcripts uploaded

chunks: $(CHUNKCSVS) $(CHUNKDBS)
	@echo chunks uploaded 

test:
	$(PSQL) < inventory.sql

resetdb:
	$(PSQL) -f droptables.sql
	rm -f .cache/*.db *.db .cache/transcripts/*.db .cache/chunks/*.db
clean: resetdb
	rm -rf $(TARGETS) $(RFCSVS) $(CSVS) $(CODS) $(DBS) $(CACHEDDBS) 
	rm -rf .cache/transcripts .cache/chunks
reallyclean: clean
	rm -rf $(CACHEDCSVS) 


