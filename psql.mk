.PHONY: all dump test login resetdb clean reallyclean tr chunks embeddings uploadembeds dumpids query
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
CACHEDDBS := $(addprefix data/,$(DBS))
CACHEDCSVS := $(addprefix data/,$(CSVS))
TARGETS:= $(CSVS) $(addsuffix .html, $(PODCASTS))
PSQL := psql -X -v ON_ERROR_STOP=1 -U pgabriel radiofrance
TRCSVS:=$(addsuffix .csv,$(addprefix data/transcripts/,$(basename $(shell ls ./transcripts/))))
TRCDBS:=$(subst .csv,.db, $(TRCSVS))
CHUNKCSVS:=$(addsuffix .csv,$(addprefix data/chunks/,$(basename $(shell ls ./transcripts/))))
CHUNKDBS:=$(subst .csv,.db, $(CHUNKCSVS))
EMBEDCSVS:=$(addsuffix .csv,$(addprefix data/embeddings/,$(basename $(shell ls ./transcripts/))))
EMBEDBS:=$(subst .csv,.db, $(EMBEDCSVS))
# Preserve import markers created as intermediate prerequisites.
.SECONDARY: $(TRCDBS) $(CHUNKDBS) $(EMBEDBS)
all: $(CACHEDCSVS) $(CACHEDDBS) $(CODS) $(TARGETS)

data/%.csv:
	mkdir -p data/
	wget -nc -q $(GITHUBPAGE)/$(notdir $@) -O $@ || touch $@

$(CACHEDDBS): data/%.db: data/%.csv schema.sql importcatalogue.sql
	mkdir -p data/
	rm -f $@
	$(PSQL) -f schema.sql
	$(PSQL) -f importcatalogue.sql < $<
	touch $@

%.cutOffDate: data/%.db
	$(PSQL) --tuples-only -v show=$* -f cutoffdate.sql -o $@

%.db: %.rf.csv data/%.db importcatalogue.sql
	$(PSQL) -f importcatalogue.sql < $<
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

data/transcripts/%.csv: transcripts/%.json
	@mkdir -p $(@D)
	./transcript_json_to_csv.py $< --output $@

data/transcripts/%.db: data/transcripts/%.csv inserttranscript.sql
	cat $< | $(PSQL) \
	    -v episode_id=$* \
	    -f inserttranscript.sql
	@touch $@

data/chunks/%.csv: transcripts/%.json chunk_json_to_csv.py
	@mkdir -p $(dir $@)
	python chunk_json_to_csv.py $< > $@

data/chunks/%.db: data/chunks/%.csv insertchunks.sql data/transcripts/%.db
	cat $< | $(PSQL) \
	    -v episode_id=$* \
	    -f insertchunks.sql
	@touch $@

embeddings: $(CHUNKCSVS)
	python chunk_to_embeddings.py --hftoken .hftoken --output-dir data/embeddings $^

data/embeddings/%.db: data/embeddings/%.csv insertembeddings.sql data/chunks/%.db
	cat $< | $(PSQL) \
	    -v episode_id=$* \
	    -f insertembeddings.sql
	@touch $@


tr: $(TRCSVS) $(TRCDBS)
	@echo transcripts uploaded
chunks: $(CHUNKCSVS) $(CHUNKDBS)
	@echo chunks uploaded 
uploadembeds: $(EMBEDBS)
	echo uploaded all embeddings
dumpids:
	$(PSQL) -At -c "SELECT id FROM rf_external WHERE show = 'affaires-sensibles';" > affaires-sensibles.ids.csv
	$(PSQL) -At -c "SELECT id FROM rf_external WHERE show = 'le-cours-de-l-histoire';" > le-cours-de-l-histoire.ids.csv
	$(PSQL) -At -c "SELECT id FROM rf_external WHERE show = 'les-nuits-de-france-culture';" > les-nuits-de-france-culture.ids.csv
	$(PSQL) -At -c "SELECT id FROM rf_external WHERE show = 'les-pieds-sur-terre';" > les-pieds-sur-terre.ids.csv
	$(PSQL) -At -c "SELECT id FROM rf_external WHERE show = 'lsd-la-serie-documentaire';" > lsd-la-serie-documentaire.ids.csv
	$(PSQL) -At -c "SELECT id FROM rf_external WHERE show = 'mecaniques-du-journalisme';" > mecaniques-du-journalisme.ids.csv

query:
	$(eval querytext:= "Pompidou")
	@$(eval queryvector:=$(shell python query_to_embedding.py --hftoken .hftoken $(querytext)))
	@$(PSQL) -v queryvector=$(queryvector) < query.sql

test:
	$(PSQL) < inventory.sql

resetdb:
	$(PSQL) -f droptables.sql
	rm -f $(DBS) $(CACHEDDBS) data/transcripts/*.db data/chunks/*.db data/embeddings/*.db
clean: resetdb
	rm -rf $(TARGETS) $(RFCSVS) $(CSVS) $(CODS) 
reallyclean: clean
	rm -rf $(CACHEDCSVS)
