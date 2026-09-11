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
TARGETS:= $(addsuffix .html, $(FIPODCASTS) $(FCPODCASTS))
all: $(TARGETS)
%.db: %.csv
	sqlite3 $@ < schema.sql
	sqlite3 $@ ".import --csv --skip 1 $< rf"
	sqlite3 $@ < addlinktags.sql
%.db.csv: %.db
	sqlite3 -init sqlite3.csv.init $< < query.sql > $@

login: lsd-la-serie-documentaire.db
	sqlite3 -init sqlite3.csv.init lsd-la-serie-documentaire.db

$(addsuffix .csv,$(FIPODCASTS)):
	python rf_dump.py --api-key $(KEY) --show-url $(FI_URL)/$(@:.csv=) --out $@


$(addsuffix .csv,$(FCPODCASTS)):
	python rf_dump.py --api-key $(KEY) --show-url $(FC_URL)/$(@:.csv=) --out $@

%.html: %.db.csv
	echo '<link rel="stylesheet" href="index.css">' > $@
	python csv2html.py < $< >> $@
test:
	@echo $(TARGETS)
	@echo $(addsuffix .csv,$(FCPODCASTS))
clean:
	rm -rf $(TARGETS)
	rm -rf $(addsuffix .db,$(basename $(TARGETS)))
	rm -rf $(addsuffix .db.csv,$(basename $(TARGETS)))

